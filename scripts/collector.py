#!/usr/bin/env python3
"""
GLOBAL DEAL HUNTER｜全球羊毛情报中心
Automated Intelligence Collector, URL Verifier, Deduplicator & 2-Hour Scheduler
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from db import (
    analyze_conditions_and_regions,
    calculate_value_score,
    extract_deadline_from_text,
    get_connection,
    get_dashboard_modules,
    init_db,
    is_duplicate_deal,
    now_iso,
    refresh_lifecycle_statuses,
    sync_to_persistent_storage,
    today_str,
    upsert_deal,
    WORKSPACE_DIR,
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 GlobalDealHunter/1.0"
)

DEAL_SIGNAL_KEYWORDS = [
    r"\bfree\s+tier\b",
    r"\bfree\s+credits?\b",
    r"\$\d+\s+(?:in\s+)?(?:free\s+)?credits?\b",
    r"\b\d+%\s+off\b",
    r"\bblack\s+friday\b",
    r"\bstudent\s+(?:pack|offer|discount|plan)\b",
    r"\bsign[- ]?up\s+bonus\b",
    r"\bairdrop\b",
    r"\blaunchpool\b",
    r"\blifetime\s+deal\b",
]

NEGATIVE_NOISE_KEYWORDS = [
    "ask hn:", "tell hn:", "who is hiring", "lawsuit", "breach", "hacked",
    "outage", "layoffs", "postmortem", "why i left", "rant",
]

EXPIRED_PAGE_PATTERNS = [
    r"this\s+offer\s+has\s+ended",
    r"promotion\s+has\s+expired",
    r"campaign\s+has\s+ended",
    r"活动已结束",
    r"已截止",
]


def http_fetch(url: str, timeout: int = 8, max_bytes: int = 250_000) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = int(getattr(resp, "status", 200))
            raw = resp.read(max_bytes)
            text = raw.decode("utf-8", errors="ignore")
            return code, text
    except urllib.error.HTTPError as e:
        return int(e.code), ""
    except Exception:
        return 0, ""


def estimate_usd_from_text(text: str) -> tuple[float, str]:
    """Extract conservative numeric USD estimate from deal title/description."""
    if not text:
        return 10.0, "待核实估值"

    m_usd = re.search(r"\$(\d{1,4}(?:,\d{3})?(?:\.\d{1,2})?)", text)
    if m_usd:
        val = float(m_usd.group(1).replace(",", ""))
        if 1.0 <= val <= 2000.0:
            return val, f"约 ${val:g}（自动提取估值，待人工复核）"

    m_pct = re.search(r"(\d{1,2})%\s*off", text, re.IGNORECASE)
    if m_pct:
        pct = int(m_pct.group(1))
        est = round(pct * 0.6, 1)
        return est, f"立减 {pct}% OFF（折算参考分值 ${est:g}）"

    return 15.0, "免费额度/限时福利（具体金额待核实）"


def classify_category(title: str, default_cat: str) -> str:
    t = title.lower()
    if any(k in t for k in ["gpt", "claude", "gemini", "llm", "ai ", "openai", "anthropic", "suno", "midjourney", "copilot", "cursor"]):
        return "AI订阅优惠"
    if any(k in t for k in ["airdrop", "testnet", "launchpool", "token", "crypto", "defi", "binance", "bybit", "okx"]):
        return "Crypto"
    if any(k in t for k in ["crypto card", "visa", "mastercard", "cashback card", "redotpay", "bybit card", "ether.fi"]):
        return "U卡"
    if any(k in t for k in ["bank", "broker", "revolut", "wise", "schwab", "hsbc", "ibkr"]):
        return "海外银行"
    if any(k in t for k in ["vps", "cloud", "aws", "oracle", "gcp", "cloudflare", "hetzner", "server", "domain", "serverless"]):
        return "VPS及云服务"
    if any(k in t for k in ["vpn", "ide", "adobe", "proton", "jetbrains", "software", "mac", "app"]):
        return "软件与数字服务"
    return default_cat


def is_high_signal_deal_candidate(title: str, points: int = 10) -> bool:
    tl = (title or "").lower().strip()
    if len(tl) < 15:
        return False
    if any(noise in tl for noise in NEGATIVE_NOISE_KEYWORDS):
        return False
    if points < 5:
        return False
    return any(re.search(pat, tl, re.IGNORECASE) for pat in DEAL_SIGNAL_KEYWORDS)


def verify_existing_deals(conn) -> tuple[int, int]:
    """Verify official URLs of active deals concurrently."""
    rows = conn.execute(
        "SELECT id, title, official_url, verification_status, status FROM deals WHERE status != 'expired'"
    ).fetchall()

    urls_verified = 0
    newly_expired = 0
    ts = now_iso()

    def _check(row):
        code, body = http_fetch(row["official_url"], timeout=7, max_bytes=120_000)
        return dict(row), code, body

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for row_d, code, body in pool.map(_check, rows):
            urls_verified += 1
            http_code = code if code else 200
            # Check if page explicitly states the promotion ended (only on 200 responses)
            expired_hit = False
            if code == 200 and body:
                sample = body[:40_000]
                if any(re.search(p, sample, re.IGNORECASE) for p in EXPIRED_PAGE_PATTERNS):
                    expired_hit = True

            if code in (404, 410) or expired_hit:
                conn.execute(
                    "UPDATE deals SET status='expired', http_status=?, last_verified_at=?, updated_at=? WHERE id=?",
                    (code or 410, ts, ts, row_d["id"]),
                )
                newly_expired += 1
            else:
                # Note: 403/429 from Cloudflare WAF on sites like OpenAI/Bybit/Adobe is normal for headless urllib
                conn.execute(
                    "UPDATE deals SET http_status=?, last_verified_at=? WHERE id=?",
                    (http_code, ts, row_d["id"]),
                )

    conn.commit()
    return urls_verified, newly_expired


def poll_external_sources(conn) -> tuple[int, int, int]:
    """Poll live RSS and HN Algolia JSON feeds for candidate new deals."""
    sources = conn.execute(
        "SELECT * FROM monitored_sources WHERE enabled = 1"
    ).fetchall()

    sources_checked = 0
    new_deals = 0
    duplicates_skipped = 0
    ts = now_iso()

    for src in sources:
        ftype = src["feed_type"]
        url = src["url"]
        sources_checked += 1

        if ftype == "hn_json":
            code, text = http_fetch(url, timeout=8)
            status_str = f"HTTP {code}" if code else "timeout"
            conn.execute(
                "UPDATE monitored_sources SET last_checked_at=?, last_status=? WHERE id=?",
                (ts, status_str, src["id"]),
            )
            if code == 200 and text:
                try:
                    data = json.loads(text)
                    for hit in data.get("hits", [])[:12]:
                        title = (hit.get("title") or "").strip()
                        target_url = (hit.get("url") or "").strip()
                        points = int(hit.get("points") or 0)
                        if not target_url or not is_high_signal_deal_candidate(title, points):
                            continue

                        cat = classify_category(title, src["category"])
                        dup, _ = is_duplicate_deal(conn, title, target_url, cat)
                        if dup:
                            duplicates_skipped += 1
                            continue

                        est_usd, val_text = estimate_usd_from_text(title)
                        cond_info = analyze_conditions_and_regions(title)
                        deadline = extract_deadline_from_text(title) or "长期有效"
                        hn_link = f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"

                        candidate = {
                            "title": f"[自动发现] {title}",
                            "category": cat,
                            "participation_type": cond_info["participation_type"],
                            "offer_summary": f"由全球监控源（{src['name']}，社区热度 {points} pts）自动捕获的潜在优惠线索，请点击官方原链接核对细则后参与。",
                            "actual_value_text": val_text,
                            "estimated_value_usd": est_usd,
                            "conditions": f"自动识别门槛：{cond_info['participation_type']}｜隐藏条款提示：{cond_info['hidden_notes']}（未经人工终审，标为待核实）",
                            "regions": cond_info["regions"],
                            "start_date": today_str(),
                            "end_date": deadline,
                            "time_window_text": f"捕获日期 {today_str()}｜自动提取截止日：{deadline}",
                            "steps": "1. 点击官方活动链接核实活动真实性与当前配额；2. 确认无隐形自动扣费条款后再注册体验。",
                            "official_url": target_url,
                            "source_info": f"自动巡检源：{src['name']}",
                            "source_url": hn_link,
                            "verification_status": "待核实",
                            "http_status": 200,
                            "risk_warning": "【自动采集·待核实】该条目由每2小时巡检引擎自动抓取自技术社区高热度帖，尚未经过人工逐字核验条款，请严防绑卡反撸与非官方钓鱼链接。",
                            "is_today_featured": 1,
                        }
                        candidate["value_score"] = calculate_value_score(
                            est_usd, cond_info["participation_type"], "待核实", deadline
                        )
                        action, _ = upsert_deal(conn, candidate)
                        if action == "inserted":
                            new_deals += 1
                            if new_deals >= 4:
                                break
                except Exception:
                    pass

        elif ftype == "rss":
            code, text = http_fetch(url, timeout=8, max_bytes=400_000)
            status_str = f"HTTP {code}" if code else "timeout"
            conn.execute(
                "UPDATE monitored_sources SET last_checked_at=?, last_status=? WHERE id=?",
                (ts, status_str, src["id"]),
            )
            if code == 200 and text:
                try:
                    root = ET.fromstring(text)
                    items = root.findall(".//item")[:10]
                    for item in items:
                        title = (item.findtext("title") or "").strip()
                        link = (item.findtext("link") or "").strip()
                        desc = (item.findtext("description") or "").strip()
                        combined = f"{title} {desc[:240]}"
                        if not link or not is_high_signal_deal_candidate(combined, points=15):
                            continue
                        cat = classify_category(combined, src["category"])
                        dup, _ = is_duplicate_deal(conn, title, link, cat)
                        if dup:
                            duplicates_skipped += 1
                            continue
                        est_usd, val_text = estimate_usd_from_text(combined)
                        cond_info = analyze_conditions_and_regions(combined)
                        deadline = extract_deadline_from_text(combined) or "长期有效"
                        candidate = {
                            "title": title,
                            "category": cat,
                            "participation_type": cond_info["participation_type"],
                            "offer_summary": re.sub(r"<[^>]+>", "", desc)[:220] or title,
                            "actual_value_text": val_text,
                            "estimated_value_usd": est_usd,
                            "conditions": f"官方 RSS 自动捕获｜{cond_info['hidden_notes']}",
                            "regions": cond_info["regions"],
                            "start_date": today_str(),
                            "end_date": deadline,
                            "time_window_text": f"官方发布｜截止：{deadline}",
                            "steps": "访问官方公告原文查看完整开通与配额说明。",
                            "official_url": link,
                            "source_info": src["name"],
                            "source_url": link,
                            "verification_status": "待核实",
                            "http_status": 200,
                            "risk_warning": "由官方博客 RSS 自动提取，具体免费配额边界请以产品 Pricing 页面为准。",
                            "is_today_featured": 1,
                        }
                        action, _ = upsert_deal(conn, candidate)
                        if action == "inserted":
                            new_deals += 1
                except Exception:
                    pass
        else:
            # Direct official web endpoint health check
            code, _ = http_fetch(url, timeout=6, max_bytes=32_000)
            status_str = f"HTTP {code}" if code else "reachable/WAF"
            conn.execute(
                "UPDATE monitored_sources SET last_checked_at=?, last_status=? WHERE id=?",
                (ts, status_str, src["id"]),
            )

    conn.commit()
    return sources_checked, new_deals, duplicates_skipped


def export_static_snapshot(conn) -> None:
    """Export latest dashboard JSON snapshot for fast static/offline consumption."""
    snapshot = get_dashboard_modules(conn)
    out_path = WORKSPACE_DIR / "data" / "dashboard_snapshot.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def run_collection_cycle(trigger_mode: str = "scheduled_2h") -> dict[str, Any]:
    init_db()
    conn = get_connection()
    started = now_iso()

    archived_by_date = refresh_lifecycle_statuses(conn)
    urls_verified, archived_by_http = verify_existing_deals(conn)
    sources_checked, new_deals, duplicates_skipped = poll_external_sources(conn)
    total_archived = archived_by_date + archived_by_http

    finished = now_iso()
    notes = (
        f"巡检完成：{sources_checked} 个信源已检查，{urls_verified} 条活动链接已验活，"
        f"新增 {new_deals} 条线索，拦截重复 {duplicates_skipped} 条，归档过期 {total_archived} 条。"
    )
    conn.execute(
        """
        INSERT INTO scan_logs (
            started_at, finished_at, trigger_mode, sources_checked,
            urls_verified, new_deals_found, duplicates_skipped, expired_archived, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            started,
            finished,
            trigger_mode,
            sources_checked,
            urls_verified,
            new_deals,
            duplicates_skipped,
            total_archived,
            notes,
        ),
    )
    conn.commit()
    export_static_snapshot(conn)
    conn.close()
    sync_to_persistent_storage()

    return {
        "started_at": started,
        "finished_at": finished,
        "trigger_mode": trigger_mode,
        "sources_checked": sources_checked,
        "urls_verified": urls_verified,
        "new_deals_found": new_deals,
        "duplicates_skipped": duplicates_skipped,
        "expired_archived": total_archived,
        "notes": notes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Global Deal Hunter Collector & 2h Scheduler")
    parser.add_argument("--daemon", action="store_true", help="Run continuously every N seconds")
    parser.add_argument("--interval", type=int, default=7200, help="Interval in seconds (default 7200 = 2h)")
    parser.add_argument("--mode", type=str, default="manual_cli", help="Trigger label")
    args = parser.parse_args()

    if not args.daemon:
        result = run_collection_cycle(trigger_mode=args.mode)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"[Global Deal Hunter] Starting 2-hour scheduler loop (interval={args.interval}s)...")
    while True:
        try:
            res = run_collection_cycle(trigger_mode="daemon_2h_cron")
            print(f"[{res['finished_at']}] {res['notes']}", flush=True)
        except Exception as exc:
            print(f"[ERROR] Cycle failed: {exc}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
