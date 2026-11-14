"""SQLite 访问：站点配置、扫描结果（幂等写入）。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from . import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sites (
    name TEXT PRIMARY KEY,
    base_url TEXT NOT NULL,
    max_pages INTEGER NOT NULL DEFAULT 10,
    policy TEXT NOT NULL DEFAULT 'strict',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
    scan_key TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    run_count INTEGER NOT NULL DEFAULT 1,
    target TEXT NOT NULL,
    max_pages INTEGER NOT NULL,
    policy TEXT NOT NULL,
    stop_reason TEXT NOT NULL DEFAULT '',
    stop_page INTEGER NOT NULL DEFAULT 0,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    catalog_count INTEGER NOT NULL DEFAULT 0,
    sitemap_count INTEGER NOT NULL DEFAULT 0,
    only_catalog_count INTEGER NOT NULL DEFAULT 0,
    only_sitemap_count INTEGER NOT NULL DEFAULT 0,
    mismatch_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    result_json TEXT NOT NULL
);
"""


def scan_key(target: str, max_pages: int, policy: str) -> str:
    raw = "%s|%d|%s" % (target, max_pages, policy)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    path = settings.db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_sites(conn)
    return conn


def _seed_sites(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT value FROM meta WHERE key='seed_version'").fetchone()
    if row and row["value"] == settings.SEED_VERSION:
        return
    profiles: list[dict] = []
    if settings.SITE_PROFILES_FILE.exists():
        try:
            profiles = json.loads(settings.SITE_PROFILES_FILE.read_text(encoding="utf-8")).get("sites", [])
        except (ValueError, OSError):
            profiles = []
    now = _now()
    for item in profiles:
        conn.execute(
            "INSERT OR IGNORE INTO sites(name, base_url, max_pages, policy, note, created_at) VALUES (?,?,?,?,?,?)",
            (
                item.get("name", "样例站"),
                item.get("base_url", settings.DEFAULT_SAMPLE_SITE_URL),
                int(item.get("max_pages", settings.DEFAULT_MAX_PAGES)),
                item.get("policy", "strict"),
                item.get("note", ""),
                now,
            ),
        )
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('seed_version', ?)", (settings.SEED_VERSION,))
    conn.commit()


def list_sites() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM sites ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def save_scan(payload: dict) -> str:
    """按 (target, max_pages, policy) 幂等写入：同一次扫描重跑只更新计数，不新增行。"""
    key = scan_key(payload["target"], int(payload["max_pages"]), payload["policy"])
    payload["scan_id"] = key
    now = _now()
    pagination = payload["pagination"]
    diff = payload["diff"]["counts"]
    failure_total = sum(payload["failures"]["counts"].values())
    with _connect() as conn:
        existing = conn.execute("SELECT created_at FROM scans WHERE scan_key=?", (key,)).fetchone()
        created = existing["created_at"] if existing else now
        conn.execute(
            """
            INSERT INTO scans(scan_key, created_at, updated_at, run_count, target, max_pages, policy,
                              stop_reason, stop_page, pages_fetched, catalog_count, sitemap_count,
                              only_catalog_count, only_sitemap_count, mismatch_count, failure_count, result_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(scan_key) DO UPDATE SET
                updated_at=excluded.updated_at,
                run_count=scans.run_count + 1,
                stop_reason=excluded.stop_reason,
                stop_page=excluded.stop_page,
                pages_fetched=excluded.pages_fetched,
                catalog_count=excluded.catalog_count,
                sitemap_count=excluded.sitemap_count,
                only_catalog_count=excluded.only_catalog_count,
                only_sitemap_count=excluded.only_sitemap_count,
                mismatch_count=excluded.mismatch_count,
                failure_count=excluded.failure_count,
                result_json=excluded.result_json
            """,
            (
                key,
                created,
                now,
                1,
                payload["target"],
                int(payload["max_pages"]),
                payload["policy"],
                pagination["reason"],
                int(pagination["stop_page"]),
                int(pagination["pages_fetched"]),
                diff["catalog"],
                diff["sitemap"],
                diff["only_in_catalog"],
                diff["only_in_sitemap"],
                diff["lastmod_mismatch"],
                failure_total,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT run_count, created_at, updated_at FROM scans WHERE scan_key=?", (key,)).fetchone()
    payload["run_count"] = row["run_count"]
    payload["created_at"] = row["created_at"]
    payload["updated_at"] = row["updated_at"]
    return key


def get_scan(key: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM scans WHERE scan_key=?", (key,)).fetchone()
    if not row:
        return None
    payload = json.loads(row["result_json"])
    payload["scan_id"] = row["scan_key"]
    payload["run_count"] = row["run_count"]
    payload["created_at"] = row["created_at"]
    payload["updated_at"] = row["updated_at"]
    return payload


def list_scans(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT scan_key, created_at, updated_at, run_count, target, max_pages, policy,
                   stop_reason, stop_page, pages_fetched, catalog_count, sitemap_count,
                   only_catalog_count, only_sitemap_count, mismatch_count, failure_count
            FROM scans ORDER BY updated_at DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]
