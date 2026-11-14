#!/usr/bin/env python3
"""
GLOBAL DEAL HUNTER｜全球羊毛情报中心
Cloud Database Sync Engine -> Permanent Google Spreadsheet (All 8 Modules A-H + Overview)
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from db import CONFIG_PATH, get_dashboard_modules, now_iso, PERSIST_DIR

SHEET_TABS = [
    ("00_情报总览", "总览"),
    ("A_今日最新优惠", "A"),
    ("B_高价值优惠排行榜", "B"),
    ("C_AI工具免费与折扣专区", "C"),
    ("D_Crypto空投与积分专区", "D"),
    ("E_海外银行与银行卡优惠", "E"),
    ("F_VPS及云服务专区", "F"),
    ("G_即将截止的活动", "G"),
    ("H_已结束活动档案", "H"),
    ("I_全量情报主数据库", "ALL"),
]

DEAL_HEADERS = [
    "活动名称",
    "所属类别",
    "核实状态与门槛性质",
    "优惠内容",
    "实际价值（含USD折算/评分）",
    "参与条件",
    "适用国家或地区",
    "开始及截止时间",
    "参与步骤",
    "官方活动链接",
    "信息来源",
    "最后验证时间",
    "风险提示",
]


def run_gsheets(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["gsheets", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg: dict[str, Any]) -> None:
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_spreadsheet() -> tuple[str, str]:
    cfg = load_config()
    sp_id = cfg.get("spreadsheet_id", "")
    sp_url = cfg.get("spreadsheet_url", "")

    if sp_id:
        code, out, _ = run_gsheets(["readonly", "info", sp_id])
        if code == 0:
            return sp_id, sp_url or f"https://docs.google.com/spreadsheets/d/{sp_id}/edit"

    code, out, err = run_gsheets(
        ["mutate", "create", "--title", "GLOBAL DEAL HUNTER｜全球羊毛情报中心（云端实时情报库）"]
    )
    if code != 0:
        raise RuntimeError(f"Failed to create Google Sheet: {err or out}")

    combined = f"{out}\n{err}"
    m_url = re.search(r"https://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)[^\s]*", combined)
    if m_url:
        sp_id = m_url.group(1)
        sp_url = f"https://docs.google.com/spreadsheets/d/{sp_id}/edit"
    else:
        m_id = re.search(r"([a-zA-Z0-9_-]{25,})", combined)
        if not m_id:
            raise RuntimeError(f"Could not parse Spreadsheet ID from output: {combined}")
        sp_id = m_id.group(1)
        sp_url = f"https://docs.google.com/spreadsheets/d/{sp_id}/edit"

    # Rename sheet 0 to first tab, and add the remaining tabs
    run_gsheets(["mutate", "rename-sheet", sp_id, "--sheet-id", "0", "--title", SHEET_TABS[0][0]])
    for tab_title, _ in SHEET_TABS[1:]:
        run_gsheets(["mutate", "add-sheet", sp_id, "--title", tab_title])

    # Fetch sheet IDs to freeze top row & apply clean header formatting
    code_s, sheets_out, _ = run_gsheets(["readonly", "list-sheets", sp_id, "--json"])
    if code_s == 0 and sheets_out:
        try:
            sheets_meta = json.loads(sheets_out)
            items = sheets_meta if isinstance(sheets_meta, list) else sheets_meta.get("sheets", [])
            for sh in items:
                sid = sh.get("sheetId") if "sheetId" in sh else sh.get("id")
                if sid is not None:
                    run_gsheets(["mutate", "freeze", sp_id, "--sheet-id", str(sid), "--rows", "1"])
                    run_gsheets([
                        "mutate", "format", sp_id,
                        "--sheet-id", str(sid),
                        "--start-row", "0", "--end-row", "1",
                        "--start-col", "0", "--end-col", "13",
                        "--bold", "--bg-color", "#0F172A", "--fg-color", "#F8FAFC", "--wrap",
                    ])
        except Exception:
            pass

    cfg["spreadsheet_id"] = sp_id
    cfg["spreadsheet_url"] = sp_url
    cfg["created_at"] = now_iso()
    save_config(cfg)
    return sp_id, sp_url


def deal_to_sheet_row(d: dict[str, Any]) -> list[str]:
    badge = f"【{d['verification_status']}｜{d['participation_type']}】"
    val_str = f"{d['actual_value_text']}（估值 ${d['estimated_value_usd']:g}｜评分 {d['value_score']}/100）"
    return [
        str(d.get("title", "")),
        str(d.get("category", "")),
        badge,
        str(d.get("offer_summary", "")),
        val_str,
        str(d.get("conditions", "")),
        str(d.get("regions", "")),
        str(d.get("time_window_text", "")),
        str(d.get("steps", "")),
        str(d.get("official_url", "")),
        str(d.get("source_info", "")),
        f"{d.get('last_verified_at', '')} (HTTP {d.get('http_status', 200)})",
        str(d.get("risk_warning", "")),
    ]


def sync_all_to_gsheets() -> dict[str, str]:
    sp_id, sp_url = ensure_spreadsheet()
    dash = get_dashboard_modules()
    kpis = dash["kpis"]

    overview_rows = [
        ["GLOBAL DEAL HUNTER｜全球羊毛情报中心", "云端主数据库与8大模块实时看板", "最后同步时间", dash["generated_at"]],
        ["指标名称", "当前数值", "自动化工作流策略", "运行状态"],
        ["有效全球优惠总数", str(kpis["active_count"]), "每2小时增量巡检与链接验活", "运行中（守护进程已激活）"],
        ["今日/近期最新优惠 (A)", str(kpis["today_new_count"]), "SHA-256 指纹 + 标题相似度去重", "已启用"],
        ["真正零撸/学生免费数", str(kpis["truly_free_count"]), "截止日期自动提取与倒计时预警", "已启用"],
        ["即将截止活动数 (G)", str(kpis["expiring_soon_count"]), "到期活动自动移入 H_已结束活动档案", "已启用"],
        ["已归档历史活动数 (H)", str(kpis["archived_count"]), "未证实社区线索强制标注「待核实」", "严格执行"],
        ["待核实线索数", str(kpis["pending_verify_count"]), "监控信源总数", str(kpis["monitored_sources_count"])],
        ["已核实活动累计参考总价值 (USD)", f"${kpis['total_verified_value_usd']:,.2f}", "高价值性价比评分加权优先展示", "已启用"],
        ["", "", "", ""],
        ["分表导航代码", "模块名称", "收录条目数", "模块说明"],
    ]
    for code_key in ["A", "B", "C", "D", "E", "F", "G", "H"]:
        mod = dash["modules"][code_key]
        overview_rows.append([
            f"模块 {code_key}",
            mod["name"],
            str(len(mod["items"])),
            mod["subtitle"],
        ])

    batch_ops: list[dict[str, Any]] = [
        {"op": "write", "range": "00_情报总览!A1", "data": overview_rows}
    ]

    for tab_title, mod_key in SHEET_TABS[1:]:
        if mod_key == "ALL":
            items = dash["all_active"] + dash["modules"]["H"]["items"]
        else:
            items = dash["modules"][mod_key]["items"]
        empty_pad = [[""] * len(DEAL_HEADERS) for _ in range(25)]
        rows = [DEAL_HEADERS] + [deal_to_sheet_row(d) for d in items] + empty_pad
        batch_ops.append({"op": "write", "range": f"{tab_title}!A1", "data": rows})

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
        json.dump(batch_ops, tf, ensure_ascii=False)
        tmp_path = tf.name

    try:
        code, out, err = run_gsheets(["mutate", "batch", sp_id, "-f", tmp_path])
        if code != 0:
            raise RuntimeError(f"Batch write failed: {err or out}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    cfg = load_config()
    cfg["last_gsheets_sync"] = now_iso()
    save_config(cfg)

    return {"spreadsheet_id": sp_id, "spreadsheet_url": sp_url, "synced_at": cfg["last_gsheets_sync"]}


if __name__ == "__main__":
    res = sync_all_to_gsheets()
    print(json.dumps(res, ensure_ascii=False, indent=2))
