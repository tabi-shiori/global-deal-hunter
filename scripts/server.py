#!/usr/bin/env python3
"""
GLOBAL DEAL HUNTER｜全球羊毛情报中心
Full-Stack HTTP Server + REST API + Embedded 2-Hour Automated Scheduler Daemon
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from collector import classify_category, estimate_usd_from_text, run_collection_cycle
from db import (
    analyze_conditions_and_regions,
    calculate_value_score,
    extract_deadline_from_text,
    get_connection,
    get_dashboard_modules,
    init_db,
    is_duplicate_deal,
    now_iso,
    now_utc,
    sync_to_persistent_storage,
    today_str,
    upsert_deal,
    WORKSPACE_DIR,
)
from sync_gsheets import sync_all_to_gsheets

WEB_DIR = WORKSPACE_DIR / "web"
PORT = int(os.environ.get("GDH_PORT", "8787"))
SCAN_INTERVAL_SECONDS = 7200  # 2 hours

SCHEDULER_STATE = {
    "running": True,
    "interval_seconds": SCAN_INTERVAL_SECONDS,
    "started_at": now_iso(),
    "last_run_at": now_iso(),
    "next_run_epoch": time.time() + SCAN_INTERVAL_SECONDS,
    "scan_in_progress": False,
    "cycles_completed": 1,
}
SCAN_LOCK = threading.Lock()


def execute_safe_scan(trigger_mode: str) -> dict:
    if not SCAN_LOCK.acquire(blocking=False):
        return {"busy": True, "notes": "已有巡检任务正在执行中，请稍候…"}
    SCHEDULER_STATE["scan_in_progress"] = True
    try:
        res = run_collection_cycle(trigger_mode=trigger_mode)
        try:
            sync_all_to_gsheets()
        except Exception:
            pass
        SCHEDULER_STATE["last_run_at"] = res["finished_at"]
        SCHEDULER_STATE["next_run_epoch"] = time.time() + SCAN_INTERVAL_SECONDS
        SCHEDULER_STATE["cycles_completed"] += 1
        return res
    finally:
        SCHEDULER_STATE["scan_in_progress"] = False
        SCAN_LOCK.release()


def background_2h_scheduler() -> None:
    while True:
        sleep_for = max(5.0, SCHEDULER_STATE["next_run_epoch"] - time.time())
        time.sleep(min(sleep_for, 30.0))
        if time.time() >= SCHEDULER_STATE["next_run_epoch"]:
            try:
                execute_safe_scan("auto_2h_daemon")
            except Exception as exc:
                print(f"[Scheduler Error] {exc}", flush=True)
                SCHEDULER_STATE["next_run_epoch"] = time.time() + SCAN_INTERVAL_SECONDS


MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class DealHunterHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send_json(self, data: dict, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 1_000_000))
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/dashboard":
            dash = get_dashboard_modules()
            next_dt = dt.datetime.fromtimestamp(
                SCHEDULER_STATE["next_run_epoch"], tz=dt.timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
            dash["scheduler"] = {
                **SCHEDULER_STATE,
                "next_run_at": next_dt,
                "seconds_until_next": max(0, int(SCHEDULER_STATE["next_run_epoch"] - time.time())),
            }
            self._send_json(dash)
            return

        if path == "/api/export.csv":
            dash = get_dashboard_modules()
            items = dash["all_active"] + dash["modules"]["H"]["items"]
            buf = io.StringIO()
            buf.write("\ufeff")
            writer = csv.writer(buf)
            writer.writerow([
                "ID", "活动名称", "所属类别", "门槛性质", "核实状态", "当前状态",
                "优惠内容", "实际价值", "USD估值", "性价比评分",
                "参与条件", "适用国家或地区", "开始及截止时间",
                "参与步骤", "官方活动链接", "信息来源", "最后验证时间", "风险提示",
            ])
            for d in items:
                writer.writerow([
                    d["id"], d["title"], d["category"], d["participation_type"],
                    d["verification_status"], d["status"], d["offer_summary"],
                    d["actual_value_text"], d["estimated_value_usd"], d["value_score"],
                    d["conditions"], d["regions"], d["time_window_text"],
                    d["steps"], d["official_url"], d["source_info"],
                    d["last_verified_at"], d["risk_warning"],
                ])
            raw = buf.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="global_deal_hunter_export.csv"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        # Serve static files from WEB_DIR
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            target = WEB_DIR / "index.html"
        if not target.is_file():
            self._send_json({"error": "Frontend not found"}, 404)
            return

        raw = target.read_bytes()
        mime = MIME_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/scan":
            res = execute_safe_scan("manual_web_trigger")
            self._send_json({"ok": True, "result": res})
            return

        if path == "/api/sync-sheets":
            try:
                res = sync_all_to_gsheets()
                self._send_json({"ok": True, "result": res})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if path == "/api/deals":
            try:
                body = self._read_json_body()
                title = (body.get("title") or "").strip()
                official_url = (body.get("official_url") or "").strip()
                offer_summary = (body.get("offer_summary") or "").strip()
                if not title or not official_url:
                    self._send_json({"ok": False, "error": "活动名称与官方链接不能为空"}, 400)
                    return

                conn = get_connection()
                combined_text = f"{title} {offer_summary} {body.get('conditions', '')}"
                cat = body.get("category") or classify_category(combined_text, "其他全球优惠")

                dup, reason = is_duplicate_deal(conn, title, official_url, cat)
                if dup:
                    conn.close()
                    self._send_json({
                        "ok": False,
                        "duplicate": True,
                        "error": f"自动去重拦截：已存在相同或高度相似优惠（{reason}）",
                    }, 409)
                    return

                cond_auto = analyze_conditions_and_regions(combined_text)
                p_type = body.get("participation_type") or cond_auto["participation_type"]
                est_usd_auto, val_text_auto = estimate_usd_from_text(combined_text)
                est_usd = float(body.get("estimated_value_usd") or est_usd_auto)
                val_text = (body.get("actual_value_text") or val_text_auto).strip()
                deadline = (body.get("end_date") or "").strip() or extract_deadline_from_text(combined_text) or "长期有效"
                v_status = body.get("verification_status") or "待核实"

                deal_obj = {
                    "title": title,
                    "category": cat,
                    "participation_type": p_type,
                    "offer_summary": offer_summary or title,
                    "actual_value_text": val_text,
                    "estimated_value_usd": est_usd,
                    "value_score": calculate_value_score(est_usd, p_type, v_status, deadline),
                    "conditions": (body.get("conditions") or f"{p_type}｜{cond_auto['hidden_notes']}").strip(),
                    "regions": (body.get("regions") or cond_auto["regions"]).strip(),
                    "start_date": today_str(),
                    "end_date": deadline,
                    "time_window_text": body.get("time_window_text") or f"{today_str()} 至 {deadline}",
                    "steps": (body.get("steps") or "访问官方活动链接核实细则后按指引参与。").strip(),
                    "official_url": official_url,
                    "source_info": (body.get("source_info") or "情报中心人工/AI录入").strip(),
                    "source_url": official_url,
                    "verification_status": v_status,
                    "risk_warning": (body.get("risk_warning") or cond_auto["hidden_notes"]).strip(),
                    "is_today_featured": 1,
                }
                action, deal_id = upsert_deal(conn, deal_obj)
                conn.close()
                sync_to_persistent_storage()
                self._send_json({"ok": True, "action": action, "id": deal_id})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if path == "/api/deals/action":
            try:
                body = self._read_json_body()
                deal_id = int(body["id"])
                act = body.get("action", "")
                conn = get_connection()
                if act == "toggle_verify":
                    cur = conn.execute("SELECT verification_status, estimated_value_usd, participation_type, end_date FROM deals WHERE id=?", (deal_id,)).fetchone()
                    if cur:
                        new_v = "已核实" if cur["verification_status"] != "已核实" else "待核实"
                        new_score = calculate_value_score(cur["estimated_value_usd"], cur["participation_type"], new_v, cur["end_date"])
                        conn.execute(
                            "UPDATE deals SET verification_status=?, value_score=?, last_verified_at=?, updated_at=? WHERE id=?",
                            (new_v, new_score, now_iso(), now_iso(), deal_id),
                        )
                elif act == "archive":
                    conn.execute(
                        "UPDATE deals SET status='expired', updated_at=? WHERE id=?",
                        (now_iso(), deal_id),
                    )
                conn.commit()
                conn.close()
                sync_to_persistent_storage()
                self._send_json({"ok": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        self._send_json({"error": "Not found"}, 404)


def main() -> None:
    init_db()
    t = threading.Thread(target=background_2h_scheduler, daemon=True)
    t.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), DealHunterHandler)
    print(f"[GLOBAL DEAL HUNTER] Live Space Server listening on http://localhost:{PORT}", flush=True)
    print(f"[GLOBAL DEAL HUNTER] 2-Hour Background Scheduler Active (interval={SCAN_INTERVAL_SECONDS}s)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
