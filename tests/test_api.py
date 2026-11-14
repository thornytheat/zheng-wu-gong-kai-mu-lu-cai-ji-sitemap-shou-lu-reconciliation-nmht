"""API 与端到端：成功、4xx、CSV 导出、幂等重跑、容器内真抓样例站。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import crawler, settings, store

from conftest import FakeFetcher, entry, list_page, sitemap_xml

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
