"""API 与端到端：成功、4xx、CSV 导出、幂等重跑、容器内真抓样例站。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import crawler, settings, store
from app.crawler import FetchResult

from conftest import FakeFetcher, entry, list_page, sitemap_index_xml, sitemap_xml

PAGE_URL = "http://sample-site/list/page-%d.html"
DETAIL = "http://sample-site/list/%s.html"


@pytest.fixture()
def client(temp_db):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def offline_site(monkeypatch):
    """假的 6 页站点：第 3 页复制第 2 页，详情 detail-03 返回 404，sitemap 缺 2 条 + 多 1 条 + 1 条日期不一致。"""
    duplicate = [entry("detail-03", "2026-02-05"), entry("detail-04", "2026-02-06")]
    pages = {
        PAGE_URL % 1: list_page([entry("detail-01", "2026-01-05"), entry("detail-02", "2026-02-04")]),
        PAGE_URL % 2: list_page(duplicate),
        PAGE_URL % 3: list_page(duplicate),
        DETAIL % "detail-01": "<html><body>detail-01</body></html>",
        DETAIL % "detail-02": "<html><body>detail-02</body></html>",
        DETAIL % "detail-04": "<html><body>detail-04</body></html>",
    }
    sitemap = sitemap_xml(
        [
            (DETAIL % "detail-01", "2026-01-05"),
            (DETAIL % "detail-02", "2026-02-20"),
            (DETAIL % "detail-99", "2026-05-03"),
        ]
    )
    fetcher = FakeFetcher(pages=pages, sitemap=sitemap)
    monkeypatch.setattr(crawler, "http_fetch", fetcher)
    return fetcher


def test_health_and_home_page(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    home = client.get("/")
    assert home.status_code == 200
    assert "政务公开目录采集" in home.text
    assert "执行采集" in home.text or "进入工作台" in home.text

    workbench = client.get("/workbench")
    assert workbench.status_code == 200
    assert "分页诊断口径" in workbench.text

    review = client.get("/review")
    assert review.status_code == 200


def test_scan_rejects_invalid_target(client):
    response = client.post("/api/scan", json={"target": "not a url", "max_pages": 10})
    assert response.status_code == 400
    assert "空白" in response.json()["detail"]

    bad_scheme = client.post("/api/scan", json={"target": "ftp://sample-site", "max_pages": 10})
    assert bad_scheme.status_code == 400


def test_scan_rejects_out_of_range_pages(client):
    response = client.post("/api/scan", json={"target": "http://sample-site", "max_pages": 0})
    assert response.status_code == 422


def test_normalize_endpoint(client):
    ok = client.post(
        "/api/normalize",
        json={"url": "/a/b/?utm_source=x&b=2&a=1#f", "base": "HTTP://Sample-Site:80"},
    )
    assert ok.status_code == 200
    assert ok.json()["normalized"] == "http://sample-site/a/b?a=1&b=2"
    assert "去 fragment" in ok.json()["changes"]

    bad = client.post("/api/normalize", json={"url": "ftp://sample-site/x"})
    assert bad.status_code == 400


def test_scan_success_offline_renders_evidence_and_csv(client, offline_site):
    response = client.post("/api/scan", json={"target": PAGE_URL % 1, "max_pages": 6, "policy": "strict"})
    assert response.status_code == 200, response.text
    data = response.json()
    summary = data["summary"]
    assert "重复页" in summary["stop_reason"]
    assert summary["stop_page"] == 3
    assert summary["pages_fetched"] == 3
    assert summary["only_in_catalog"] == 2
    assert summary["only_in_sitemap"] == 1
    assert summary["lastmod_mismatch"] == 1
    assert summary["failures"]["4xx"] == 1
    scan_id = data["scan_id"]

    page = client.get("/result/%s" % scan_id)
    assert page.status_code == 200
    assert "结论" in page.text
    assert "目录有、sitemap 缺" in page.text
    assert "重复页" in page.text

    csv_response = client.get("/review/%s/diff.csv" % scan_id)
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers["content-type"]
    body = csv_response.content.decode("utf-8-sig")
    assert "only_in_catalog" in body
    assert "detail-03" in body

    detail = client.get("/api/scan/%s" % scan_id)
    assert detail.status_code == 200
    assert detail.json()["scan_id"] == scan_id


def test_rescan_is_idempotent(client, offline_site):
    payload = {"target": PAGE_URL % 1, "max_pages": 6, "policy": "strict"}
    first = client.post("/api/scan", json=payload).json()
    second = client.post("/api/scan", json=payload).json()
    assert first["scan_id"] == second["scan_id"]

    rows = store.list_scans()
    assert len(rows) == 1
    assert rows[0]["run_count"] == 2


def test_unknown_scan_result_page_returns_404(client):
    response = client.get("/result/does-not-exist")
    assert response.status_code == 404
    assert "未找到扫描记录" in response.text


def test_unknown_csv_returns_404(client):
    assert client.get("/review/nope/diff.csv").status_code == 404


def test_e2e_real_sample_site(client):
    """容器内对 sample-site 真抓：严格模式断言重复页终止于第 3 页，容忍模式抓满 6 页。"""
    probe = crawler.http_fetch(settings.DEFAULT_SAMPLE_SITE_URL)
    if probe.error or probe.status != 200:
        pytest.skip("sample-site 不可达（%s）: %s / %s" % (settings.DEFAULT_SAMPLE_SITE_URL, probe.status, probe.error))

    strict = client.post("/api/scan", json={"target": settings.DEFAULT_SAMPLE_SITE_URL, "max_pages": 10, "policy": "strict"})
    assert strict.status_code == 200, strict.text
    strict_summary = strict.json()["summary"]
    assert "重复页" in strict_summary["stop_reason"]
    assert strict_summary["stop_page"] == 3
    assert strict_summary["pages_fetched"] == 3

    tolerant = client.post(
        "/api/scan", json={"target": settings.DEFAULT_SAMPLE_SITE_URL, "max_pages": 10, "policy": "tolerant"}
    )
    assert tolerant.status_code == 200, tolerant.text
    summary = tolerant.json()["summary"]
    assert summary["pages_fetched"] == 6
    assert summary["stop_page"] == 6
    assert "空页" in summary["stop_reason"]
    assert summary["catalog_count"] == 25
    assert summary["only_in_catalog"] == 3
    assert summary["only_in_sitemap"] == 2
    assert summary["lastmod_mismatch"] == 1
    assert summary["failures"]["外站"] == 3
    assert summary["failures"]["4xx"] == 1
    assert summary["failures"]["重复页"] == 1

    page = client.get("/result/%s" % tolerant.json()["scan_id"])
    assert page.status_code == 200
    assert "重复页" in page.text
    assert "lastmod" in page.text


# ── sitemapindex 拆分读取：全成功发布差异、部分失败只列待核对范围、重跑不变 ──

SM_A = "http://sample-site/sitemap/sitemap-a.xml"
SM_B = "http://sample-site/sitemap/sitemap/sitemap-b.xml"
SM_C = "http://sample-site/sitemap/sitemap-c.xml"


def _split_sitemap_fetcher(**overrides):
    """与 offline_site 同目录数据，但 sitemap 拆成索引 + 3 个子文件，detail-01 跨文件重复。"""
    duplicate = [entry("detail-03", "2026-02-05"), entry("detail-04", "2026-02-06")]
    pages = {
        PAGE_URL % 1: list_page([entry("detail-01", "2026-01-05"), entry("detail-02", "2026-02-04")]),
        PAGE_URL % 2: list_page(duplicate),
        PAGE_URL % 3: list_page(duplicate),
        DETAIL % "detail-01": "<html><body>detail-01</body></html>",
        DETAIL % "detail-02": "<html><body>detail-02</body></html>",
        DETAIL % "detail-04": "<html><body>detail-04</body></html>",
    }
    index = sitemap_index_xml([(SM_A, "2026-05-01"), (SM_B, "2026-05-01"), (SM_C, "2026-05-01")])
    files = {
        SM_A: sitemap_xml(
            [
                (DETAIL % "detail-01", "2026-01-05"),
                (DETAIL % "detail-02", "2026-02-20"),
            ]
        ),
        SM_B: sitemap_xml(
            [
                (DETAIL % "detail-01", "2026-01-05"),  # 与 a 重复，lastmod 相同，a 胜出
                (DETAIL % "detail-99", "2026-05-03"),
            ]
        ),
        SM_C: sitemap_xml([]),
    }
    files.update(overrides.pop("sitemap_files", {}))
    return FakeFetcher(pages=pages, sitemap=index, sitemap_files=files, **overrides)


def test_split_sitemap_all_readable_publishes_final_diff(client, monkeypatch):
    fetcher = _split_sitemap_fetcher()
    monkeypatch.setattr(crawler, "http_fetch", fetcher)

    response = client.post("/api/scan", json={"target": PAGE_URL % 1, "max_pages": 6, "policy": "strict"})
    assert response.status_code == 200, response.text
    data = response.json()
    summary = data["summary"]

    assert summary["sitemap_complete"] is True
    assert summary["sitemap_files"] == 4  # 根索引 + 3 个子文件
    assert summary["sitemap_count"] == 3  # detail-01 跨文件去重
    assert summary["only_in_catalog"] == 2
    assert summary["only_in_sitemap"] == 1
    assert summary["lastmod_mismatch"] == 1
    assert summary["verdict"] == "存在收录缺口"

    detail = client.get("/api/scan/%s" % data["scan_id"]).json()
    sm = detail["sitemap"]
    assert sm["raw_entry_total"] == 4
    assert sm["count"] == 3
    assert sm["duplicate_total"] == 1
    assert sm["arithmetic_ok"] is True
    dup = sm["duplicate_urls"][0]
    assert dup["url"].endswith("detail-01.html")
    assert dup["chosen_file"] == SM_A
    assert len(dup["sources"]) == 2
    statuses = {f["url"]: f["status"] for f in sm["files"]}
    assert statuses[SM_C] == "空文件"
    assert all(s in ("成功", "空文件") for s in statuses.values())

    page = client.get("/result/%s" % data["scan_id"])
    assert "sitemap 文件" in page.text
    assert "数目对得上" in page.text
    assert "跨文件重复链接裁决" in page.text


def test_split_sitemap_child_timeout_only_shows_pending_scope(client, monkeypatch):
    fetcher = _split_sitemap_fetcher(
        sitemap_errors={SM_B: FetchResult(SM_B, None, "", "timeout", 5000)}
    )
    monkeypatch.setattr(crawler, "http_fetch", fetcher)

    response = client.post("/api/scan", json={"target": PAGE_URL % 1, "max_pages": 6, "policy": "strict"})
    assert response.status_code == 200, response.text
    data = response.json()
    summary = data["summary"]

    assert summary["sitemap_complete"] is False
    assert summary["verdict"] == "待核对"
    # 读取不完整：三类差异一律不计数，绝不能判收录完整
    assert summary["only_in_catalog"] == 0
    assert summary["only_in_sitemap"] == 0
    assert summary["lastmod_mismatch"] == 0

    detail = client.get("/api/scan/%s" % data["scan_id"]).json()
    assert detail["diff"]["verdict"] == "待核对"
    assert "不能判为收录完整" in detail["diff"]["conclusion"]
    statuses = {f["url"]: f["status"] for f in detail["sitemap"]["files"]}
    assert statuses[SM_B] == "超时"

    page = client.get("/result/%s" % data["scan_id"])
    assert "读取不完整" in page.text
    assert "待核对范围" in page.text

    csv_response = client.get("/review/%s/diff.csv" % data["scan_id"])
    body = csv_response.content.decode("utf-8-sig")
    assert "sitemap文件：超时" in body
    assert "待核对范围" in body
    assert "only_in_catalog" not in body


def test_split_sitemap_rerun_keeps_identical_result(client, monkeypatch):
    payload = {"target": PAGE_URL % 1, "max_pages": 6, "policy": "strict"}
    monkeypatch.setattr(crawler, "http_fetch", _split_sitemap_fetcher())
    first = client.post("/api/scan", json=payload).json()
    monkeypatch.setattr(crawler, "http_fetch", _split_sitemap_fetcher())
    second = client.post("/api/scan", json=payload).json()

    assert first["scan_id"] == second["scan_id"]
    rows = store.list_scans()
    assert len(rows) == 1
    assert rows[0]["run_count"] == 2

    first_detail = client.get("/api/scan/%s" % first["scan_id"]).json()
    second_detail = client.get("/api/scan/%s" % second["scan_id"]).json()
    assert first_detail["diff"] == second_detail["diff"]
    assert first_detail["sitemap"]["files"] == second_detail["sitemap"]["files"]
    assert first_detail["sitemap"]["duplicate_urls"] == second_detail["sitemap"]["duplicate_urls"]
    assert first_detail["sitemap"]["count"] == second_detail["sitemap"]["count"]


def test_pending_scan_is_persisted_and_visible_on_review_pages(client, monkeypatch):
    fetcher = _split_sitemap_fetcher(
        sitemap_errors={SM_B: FetchResult(SM_B, 500, "", None, 2)}
    )
    monkeypatch.setattr(crawler, "http_fetch", fetcher)
    scan_id = client.post(
        "/api/scan", json={"target": PAGE_URL % 1, "max_pages": 6, "policy": "strict"}
    ).json()["scan_id"]

    rows = store.list_scans()
    assert rows[0]["sitemap_complete"] == 0
    assert rows[0]["only_catalog_count"] == 0  # 待核对不发布差异

    review = client.get("/review")
    assert review.status_code == 200
    assert "待核对" in review.text
    assert 'data-complete="0"' in review.text

    workbench = client.get("/workbench")
    assert "待核对" in workbench.text


def test_old_database_without_complete_column_is_migrated(temp_db):
    """旧版 scans 表没有 sitemap_complete 列时，连接后自动补列且旧数据默认完整。"""
    import sqlite3

    conn = sqlite3.connect(str(temp_db))
    conn.execute(
        """
        CREATE TABLE scans (
            scan_key TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, run_count INTEGER,
            target TEXT, max_pages INTEGER, policy TEXT, stop_reason TEXT, stop_page INTEGER,
            pages_fetched INTEGER, catalog_count INTEGER, sitemap_count INTEGER,
            only_catalog_count INTEGER, only_sitemap_count INTEGER, mismatch_count INTEGER,
            failure_count INTEGER, result_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO scans VALUES ('oldkey','t','t',1,'http://x',10,'tolerant','',1,1,1,1,0,0,0,0,'{}')"
    )
    conn.commit()
    conn.close()

    rows = store.list_scans()
    assert len(rows) == 1
    assert rows[0]["sitemap_complete"] == 1
