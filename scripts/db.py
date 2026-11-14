#!/usr/bin/env python3
"""
GLOBAL DEAL HUNTER｜全球羊毛情报中心
Core Database, Deduplication, Value Scoring, Condition Parser & Lifecycle Engine
"""

from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
PERSIST_DIR = Path(
    os.environ.get(
        "GDH_PERSIST_DIR",
        str(Path.home() / ".gemini" / "spaces" / "global-deal-hunter"),
    )
)
DB_PATH = WORKSPACE_DIR / "data" / "deals.db"
PERSIST_DB_PATH = PERSIST_DIR / "deals.db"
CONFIG_PATH = PERSIST_DIR / "space_config.json"

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "spm", "_hsenc", "_hsmi",
}


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def now_iso() -> str:
    return now_utc().strftime("%Y-%m-%d %H:%M UTC")


def today_str() -> str:
    return now_utc().strftime("%Y-%m-%d")


def canonicalize_url(url: str) -> str:
    """Normalize URL for deduplication while keeping meaningful query params."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        scheme = (parsed.scheme or "https").lower()
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = re.sub(r"/+$", "", parsed.path or "")
        query_items = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=False)
            if k.lower() not in TRACKING_PARAMS
        ]
        query_items.sort()
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))
    except Exception:
        return url.strip().lower()


def normalize_title(title: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (title or "").lower()).strip()
    return re.sub(r"\s+", " ", cleaned)


def compute_fingerprint(title: str, official_url: str, category: str = "") -> str:
    canon_url = canonicalize_url(official_url)
    norm_title = normalize_title(title)
    key = f"{canon_url}||{norm_title[:80]}||{category.lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def extract_deadline_from_text(text: str, reference_year: int = 2026) -> str | None:
    """Automatically extract ISO end_date (YYYY-MM-DD) from English or Chinese deal text."""
    if not text:
        return None

    # ISO dates near deadline keywords
    iso_match = re.search(
        r"(?:until|through|by|ends?|expire[sd]?|before|截止[至到]?|结束[于时间]*)\s*[:：]?\s*(202\d-\d{2}-\d{2})",
        text,
        re.IGNORECASE,
    )
    if iso_match:
        return iso_match.group(1)

    # Chinese date e.g. 2026年12月31日 or 11月20日
    cn_full = re.search(r"(202\d)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if cn_full:
        y, m, d = int(cn_full.group(1)), int(cn_full.group(2)), int(cn_full.group(3))
        return f"{y:04d}-{m:02d}-{d:02d}"

    cn_short = re.search(
        r"(?:截止|至|到|前)\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text
    )
    if cn_short:
        m, d = int(cn_short.group(1)), int(cn_short.group(2))
        return f"{reference_year:04d}-{m:02d}-{d:02d}"

    # English month names: e.g., "December 31, 2026", "31 December 2026", "Friday 20th November"
    months = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
        "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    m1 = re.search(
        r"(?:until|through|by|ends?\s+on|ends?|before)\s+(?:[a-zA-Z]+day,?\s+)?([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(202\d)?",
        text,
        re.IGNORECASE,
    )
    if m1 and m1.group(1).lower() in months:
        mo = months[m1.group(1).lower()]
        da = int(m1.group(2))
        yr = int(m1.group(3)) if m1.group(3) else reference_year
        if 1 <= da <= 31:
            return f"{yr:04d}-{mo:02d}-{da:02d}"

    m2 = re.search(
        r"(?:until|through|by|ends?\s+on|ends?|before)\s+(?:[a-zA-Z]+day,?\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]+),?\s*(202\d)?",
        text,
        re.IGNORECASE,
    )
    if m2 and m2.group(2).lower() in months:
        da = int(m2.group(1))
        mo = months[m2.group(2).lower()]
        yr = int(m2.group(3)) if m2.group(3) else reference_year
        if 1 <= da <= 31:
            return f"{yr:04d}-{mo:02d}-{da:02d}"

    return None


def analyze_conditions_and_regions(text: str) -> dict[str, str]:
    """Automatically classify participation threshold, region restrictions, and hidden traps."""
    t = (text or "").lower()

    # Participation type (strictly separating truly free, trial, deposit, volume, referral, discount)
    if any(k in t for k in ["student", "edu", "sheerid", "大学生", "教育认证", "学生"]):
        p_type = "学生/教育免费"
    elif any(k in t for k in ["trading volume", "交易量", "spot volume", "合约交易"]):
        p_type = "需要交易量"
    elif any(k in t for k in ["referral", "refer a friend", "邀请", "推荐码", "被推荐"]):
        p_type = "需要邀请条件"
    elif any(k in t for k in ["net deposit", "deposit", "stake", "lock bnb", "入金", "充值", "质押"]):
        p_type = "需要充值"
    elif any(k in t for k in ["free trial", "auto-renew", "automatically charge", "免费试用", "试用期后自动扣费"]):
        p_type = "免费试用"
    elif any(k in t for k in ["% off", "discount", "sale", "black friday", "折扣", "立减", "买断"]):
        p_type = "折扣特惠"
    elif any(k in t for k in ["spend", "cashback", "purchase", "首刷", "返现", "消费"]):
        p_type = "需要消费"
    else:
        p_type = "真正免费"

    # Region detection
    regions = []
    if any(k in t for k in ["u.s. only", "us residents", "united states only", "仅限美国", "美区"]):
        regions.append("美国 (US)")
    if any(k in t for k in ["eea", "european economic area", "欧盟", "欧洲经济区"]):
        regions.append("欧洲经济区 (EEA)")
    if any(k in t for k in ["uk", "united kingdom", "英国"]):
        regions.append("英国 (UK)")
    if not regions:
        regions.append("全球多地区（受当地合规限制）")

    # Hidden conditions
    hidden = []
    if any(k in t for k in ["auto-renew", "automatically charge", "unless cancelled", "自动续费", "自动扣费"]):
        hidden.append("到期自动按原价续费，需提前取消")
    if any(k in t for k in ["credit card", "payment method", "绑卡", "验卡"]):
        hidden.append("需绑定有效支付方式/信用卡验证")
    if any(k in t for k in ["kyc", "identity", "身份认证", "实名"]):
        hidden.append("需完成 KYC 身份及地址核验")
    if any(k in t for k in ["1 year", "hold", "maintain", "锁定期", "保留1年"]):
        hidden.append("奖励需满足资产留存期，提前提现可能追回")

    return {
        "participation_type": p_type,
        "regions": "、".join(regions),
        "hidden_notes": "；".join(hidden) if hidden else "无已知隐藏扣费条款",
    }


def calculate_value_score(
    estimated_value_usd: float,
    participation_type: str,
    verification_status: str,
    end_date: str | None = None,
) -> int:
    """
    Calculate composite Deal Value Score (0-100) for ranking high-value deals first.
    Balances USD value with zero-cost accessibility and verification trust.
    """
    import math

    # Base score from USD value (logarithmic so $1000 high-deposit bonus doesn't crush $240 free AI Pro)
    val = max(0.0, float(estimated_value_usd or 0.0))
    usd_component = min(52.0, math.log10(val + 1.0) * 19.5)

    type_weights = {
        "真正免费": 32.0,
        "学生/教育免费": 28.0,
        "免费试用": 22.0,
        "折扣特惠": 20.0,
        "需要消费": 18.0,
        "需要邀请条件": 16.0,
        "需要充值": 14.0,
        "需要交易量": 12.0,
    }
    access_component = type_weights.get(participation_type, 15.0)

    trust_component = 14.0 if verification_status == "已核实" else 4.0

    urgency_bonus = 0.0
    if end_date and re.match(r"^202\d-\d{2}-\d{2}$", end_date):
        try:
            days_left = (dt.date.fromisoformat(end_date) - now_utc().date()).days
            if 0 <= days_left <= 14:
                urgency_bonus = 2.0
        except ValueError:
            pass

    score = round(min(99.0, max(10.0, usd_component + access_component + trust_component + urgency_bonus)))
    return int(score)


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        if not db_path.exists() and PERSIST_DB_PATH.exists():
            shutil.copyfile(PERSIST_DB_PATH, db_path)
    except OSError:
        pass
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def sync_to_persistent_storage(db_path: Path = DB_PATH) -> None:
    try:
        PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        if db_path.exists() and db_path.resolve() != PERSIST_DB_PATH.resolve():
            shutil.copyfile(db_path, PERSIST_DB_PATH)
    except OSError:
        pass


def init_db(conn: sqlite3.Connection | None = None) -> None:
    close_after = False
    if conn is None:
        conn = get_connection()
        close_after = True

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            participation_type TEXT NOT NULL,
            offer_summary TEXT NOT NULL,
            actual_value_text TEXT NOT NULL,
            estimated_value_usd REAL NOT NULL DEFAULT 0,
            value_score INTEGER NOT NULL DEFAULT 50,
            conditions TEXT NOT NULL,
            regions TEXT NOT NULL,
            start_date TEXT NOT NULL DEFAULT '长期有效',
            end_date TEXT NOT NULL DEFAULT '长期有效',
            time_window_text TEXT NOT NULL,
            steps TEXT NOT NULL,
            official_url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            source_info TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            last_verified_at TEXT NOT NULL,
            verification_status TEXT NOT NULL DEFAULT '已核实',
            http_status INTEGER DEFAULT 200,
            risk_warning TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            is_today_featured INTEGER NOT NULL DEFAULT 0,
            discovered_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);
        CREATE INDEX IF NOT EXISTS idx_deals_category ON deals(category);
        CREATE INDEX IF NOT EXISTS idx_deals_score ON deals(value_score DESC);
        CREATE INDEX IF NOT EXISTS idx_deals_end_date ON deals(end_date);

        CREATE TABLE IF NOT EXISTS monitored_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            feed_type TEXT NOT NULL,
            url TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_checked_at TEXT DEFAULT '',
            last_status TEXT DEFAULT 'pending'
        );

        CREATE TABLE IF NOT EXISTS scan_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            trigger_mode TEXT NOT NULL,
            sources_checked INTEGER NOT NULL DEFAULT 0,
            urls_verified INTEGER NOT NULL DEFAULT 0,
            new_deals_found INTEGER NOT NULL DEFAULT 0,
            duplicates_skipped INTEGER NOT NULL DEFAULT 0,
            expired_archived INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.commit()
    if close_after:
        conn.close()
        sync_to_persistent_storage()


def is_duplicate_deal(conn: sqlite3.Connection, title: str, official_url: str, category: str = "") -> tuple[bool, str]:
    """Check exact fingerprint, canonical URL match, or fuzzy title similarity (>= 0.86)."""
    fp = compute_fingerprint(title, official_url, category)
    canon = canonicalize_url(official_url)
    row = conn.execute(
        "SELECT id, title FROM deals WHERE fingerprint = ?", (fp,)
    ).fetchone()
    if row:
        return True, f" fingerprint match (id={row['id']})"

    # Check same canonical URL + high title similarity
    norm_new = normalize_title(title)
    existing = conn.execute("SELECT id, title, canonical_url FROM deals").fetchall()
    for ex in existing:
        norm_ex = normalize_title(ex["title"])
        ratio = difflib.SequenceMatcher(None, norm_new, norm_ex).ratio()
        if ratio >= 0.86:
            return True, f"similar title ({ratio:.2f}) to id={ex['id']}"
        if canon and ex["canonical_url"] == canon and ratio >= 0.58:
            return True, f"same canonical URL + title match ({ratio:.2f}) to id={ex['id']}"

    return False, ""


def refresh_lifecycle_statuses(conn: sqlite3.Connection) -> int:
    """
    Automatically archive deals whose end_date has passed and flag deals expiring within 21 days.
    Returns count of newly archived deals.
    """
    today = now_utc().date()
    rows = conn.execute("SELECT id, end_date, status FROM deals").fetchall()
    archived_count = 0

    for r in rows:
        ed = (r["end_date"] or "").strip()
        old_status = r["status"]
        new_status = old_status

        if re.match(r"^202\d-\d{2}-\d{2}$", ed):
            try:
                end_dt = dt.date.fromisoformat(ed)
                days_remaining = (end_dt - today).days
                if days_remaining < 0:
                    new_status = "expired"
                elif days_remaining <= 48:
                    # Within ~48 days (covers Nov-Dec 2026 Q4/Black Friday/Year-end deadlines)
                    new_status = "expiring_soon"
                else:
                    new_status = "active"
            except ValueError:
                pass
        else:
            if old_status != "expired":
                new_status = "active"

        if new_status != old_status:
            if new_status == "expired" and old_status != "expired":
                archived_count += 1
            conn.execute(
                "UPDATE deals SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now_iso(), r["id"]),
            )

    conn.commit()
    return archived_count


def upsert_deal(conn: sqlite3.Connection, deal: dict[str, Any]) -> tuple[str, int]:
    """Insert or update a structured deal with automatic scoring and lifecycle tagging."""
    title = deal["title"].strip()
    official_url = deal["official_url"].strip()
    category = deal["category"].strip()
    canon_url = canonicalize_url(official_url)
    fp = deal.get("fingerprint") or compute_fingerprint(title, official_url, category)

    p_type = deal.get("participation_type") or analyze_conditions_and_regions(
        f"{title} {deal.get('conditions', '')}"
    )["participation_type"]
    v_status = deal.get("verification_status", "已核实")
    est_usd = float(deal.get("estimated_value_usd", 0.0))
    end_date = deal.get("end_date", "长期有效")

    score = deal.get("value_score")
    if score is None:
        score = calculate_value_score(est_usd, p_type, v_status, end_date)

    # Determine initial status from end_date
    status = deal.get("status", "active")
    if re.match(r"^202\d-\d{2}-\d{2}$", str(end_date)):
        try:
            days_left = (dt.date.fromisoformat(str(end_date)) - now_utc().date()).days
            if days_left < 0:
                status = "expired"
            elif days_left <= 48:
                status = "expiring_soon"
            else:
                status = "active"
        except ValueError:
            pass

    existing = conn.execute(
        "SELECT id FROM deals WHERE fingerprint = ?", (fp,)
    ).fetchone()

    ts = now_iso()
    if existing:
        deal_id = existing["id"]
        conn.execute(
            """
            UPDATE deals SET
                title=?, category=?, participation_type=?, offer_summary=?,
                actual_value_text=?, estimated_value_usd=?, value_score=?,
                conditions=?, regions=?, start_date=?, end_date=?, time_window_text=?,
                steps=?, official_url=?, canonical_url=?, source_info=?, source_url=?,
                last_verified_at=?, verification_status=?, http_status=?,
                risk_warning=?, status=?, is_today_featured=?, updated_at=?
            WHERE id=?
            """,
            (
                title,
                category,
                p_type,
                deal["offer_summary"],
                deal["actual_value_text"],
                est_usd,
                score,
                deal["conditions"],
                deal["regions"],
                deal.get("start_date", "长期有效"),
                end_date,
                deal["time_window_text"],
                deal["steps"],
                official_url,
                canon_url,
                deal["source_info"],
                deal.get("source_url", official_url),
                deal.get("last_verified_at", ts),
                v_status,
                int(deal.get("http_status", 200)),
                deal["risk_warning"],
                status,
                int(deal.get("is_today_featured", 0)),
                ts,
                deal_id,
            ),
        )
        conn.commit()
        return "updated", deal_id

    cur = conn.execute(
        """
        INSERT INTO deals (
            fingerprint, title, category, participation_type, offer_summary,
            actual_value_text, estimated_value_usd, value_score,
            conditions, regions, start_date, end_date, time_window_text,
            steps, official_url, canonical_url, source_info, source_url,
            last_verified_at, verification_status, http_status,
            risk_warning, status, is_today_featured, discovered_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fp,
            title,
            category,
            p_type,
            deal["offer_summary"],
            deal["actual_value_text"],
            est_usd,
            score,
            deal["conditions"],
            deal["regions"],
            deal.get("start_date", "长期有效"),
            end_date,
            deal["time_window_text"],
            deal["steps"],
            official_url,
            canon_url,
            deal["source_info"],
            deal.get("source_url", official_url),
            deal.get("last_verified_at", ts),
            v_status,
            int(deal.get("http_status", 200)),
            deal["risk_warning"],
            status,
            int(deal.get("is_today_featured", 0)),
            deal.get("discovered_at", today_str()),
            ts,
        ),
    )
    conn.commit()
    return "inserted", int(cur.lastrowid)


def enrich_deal_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    today = now_utc().date()
    ed = str(d.get("end_date", ""))
    d["days_left"] = None
    if re.match(r"^202\d-\d{2}-\d{2}$", ed):
        try:
            d["days_left"] = (dt.date.fromisoformat(ed) - today).days
        except ValueError:
            d["days_left"] = None
    return d


def get_dashboard_modules(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Build all 8 required dashboard modules (A-H) + KPI overview."""
    close_after = False
    if conn is None:
        conn = get_connection()
        close_after = True

    refresh_lifecycle_statuses(conn)
    all_rows = [
        enrich_deal_row(r)
        for r in conn.execute(
            "SELECT * FROM deals ORDER BY CASE WHEN status='expired' THEN 1 ELSE 0 END, value_score DESC, estimated_value_usd DESC"
        ).fetchall()
    ]

    active_deals = [d for d in all_rows if d["status"] != "expired"]
    expired_deals = [d for d in all_rows if d["status"] == "expired"]

    # A. 今日最新优惠 (recent launches / breaking alerts / newly captured items)
    module_a = sorted(
        [
            d for d in active_deals
            if d.get("is_today_featured") == 1
            or (re.match(r"^202\d-\d{2}-\d{2}$", str(d.get("start_date", ""))) and str(d.get("start_date", "")) >= "2026-11-10")
        ],
        key=lambda x: (x["verification_status"] == "已核实", x["value_score"], x["estimated_value_usd"]),
        reverse=True,
    )

    # B. 高价值优惠排行榜 (Top deals prioritized by value_score & verified status)
    module_b = sorted(
        [d for d in active_deals if d["estimated_value_usd"] >= 20 or d["value_score"] >= 75],
        key=lambda x: (x["verification_status"] == "已核实", x["value_score"], x["estimated_value_usd"]),
        reverse=True,
    )[:15]

    # C. AI工具免费与折扣专区
    module_c = [d for d in active_deals if d["category"] == "AI订阅优惠"]

    # D. Crypto空投与积分专区
    module_d = [d for d in active_deals if d["category"] == "Crypto"]

    # E. 海外银行与银行卡优惠 (combines 海外银行 + U卡)
    module_e = [d for d in active_deals if d["category"] in ("海外银行", "U卡")]

    # F. VPS及云服务专区 (plus developer/domain cloud infrastructure)
    module_f = [d for d in active_deals if d["category"] == "VPS及云服务"]

    # Extra helper list for Software & Global deals so all 7 collection scopes are browsable too
    module_software_other = [
        d for d in active_deals if d["category"] in ("软件与数字服务", "其他全球优惠")
    ]

    # G. 即将截止的活动 (has concrete future end_date, sorted soonest first)
    module_g = sorted(
        [
            d for d in active_deals
            if d["days_left"] is not None and 0 <= d["days_left"] <= 48
        ],
        key=lambda x: (x["days_left"], -x["value_score"]),
    )

    # H. 已结束活动档案
    module_h = sorted(
        expired_deals,
        key=lambda x: str(x.get("end_date", "")),
        reverse=True,
    )

    logs = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM scan_logs ORDER BY id DESC LIMIT 12"
        ).fetchall()
    ]
    sources = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM monitored_sources ORDER BY id ASC"
        ).fetchall()
    ]

    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

    total_verified_usd = sum(
        d["estimated_value_usd"]
        for d in active_deals
        if d["verification_status"] == "已核实"
    )
    truly_free_count = sum(
        1 for d in active_deals if d["participation_type"] in ("真正免费", "学生/教育免费")
    )
    pending_count = sum(
        1 for d in active_deals if d["verification_status"] == "待核实"
    )

    payload = {
        "space_name": "GLOBAL DEAL HUNTER｜全球羊毛情报中心",
        "generated_at": now_iso(),
        "schedule_interval_hours": 2,
        "config": cfg,
        "kpis": {
            "active_count": len(active_deals),
            "today_new_count": len(module_a),
            "truly_free_count": truly_free_count,
            "expiring_soon_count": len(module_g),
            "archived_count": len(module_h),
            "pending_verify_count": pending_count,
            "total_verified_value_usd": round(total_verified_usd, 2),
            "monitored_sources_count": len(sources),
        },
        "modules": {
            "A": {"id": "A", "name": "今日最新优惠", "subtitle": "近72小时新上线或今日新捕获的高价值全球活动", "items": module_a},
            "B": {"id": "B", "name": "高价值优惠排行榜", "subtitle": "综合实际美元估值、零撸友好度与官方核实度加权排序", "items": module_b},
            "C": {"id": "C", "name": "AI工具免费与折扣专区", "subtitle": "ChatGPT / Claude / Gemini / Suno / Midjourney / Copilot 及 API 免费额度", "items": module_c},
            "D": {"id": "D", "name": "Crypto空投与积分专区", "subtitle": "交易所 Launchpool、HODLer 空投、Token Splash、测试网与积分计划", "items": module_d},
            "E": {"id": "E", "name": "海外银行与银行卡优惠", "subtitle": "海外虚拟/实体 U卡返现开卡礼、国际银行与头部券商开户奖励", "items": module_e},
            "F": {"id": "F", "name": "VPS及云服务专区", "subtitle": "永久免费服务器、$200-$300 云计算赠金、边缘开发资源与域名福利", "items": module_f},
            "G": {"id": "G", "name": "即将截止的活动", "subtitle": "按剩余天数倒序排列，优先处理政策变更与短期窗口活动", "items": module_g},
            "H": {"id": "H", "name": "已结束活动档案", "subtitle": "到期自动归档的历史活动记录，防止误踩过期羊毛", "items": module_h},
        },
        "extra_software_global": module_software_other,
        "all_active": active_deals,
        "scan_logs": logs,
        "monitored_sources": sources,
    }

    if close_after:
        conn.close()
    return payload


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH} (persisted to {PERSIST_DB_PATH})")
