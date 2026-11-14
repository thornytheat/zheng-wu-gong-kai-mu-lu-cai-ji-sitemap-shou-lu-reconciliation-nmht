"""sitemap 对账：三类差异、日期比较口径、解析异常。"""

from __future__ import annotations

import pytest

from app.reconcile import date_part, parse_sitemap, reconcile

from conftest import sitemap_xml

CATALOG = [
    {"url": "http://sample-site/list/detail-01.html", "title": "预算公开", "lastmod": "2026-01-05", "page": 1},
    {"url": "http://sample-site/list/detail-02.html", "title": "招生意见", "lastmod": "2026-02-04", "page": 2},
    {"url": "http://sample-site/list/detail-03.html", "title": "处罚信息", "lastmod": "2026-02-05", "page": 2},
    {"url": "http://sample-site/list/detail-04.html", "title": "收费清单", "lastmod": "2026-04-02", "page": 4},
]


def build_sitemap(entries):
    return parse_sitemap(sitemap_xml(entries))


def test_three_diff_classes():
    sitemap = build_sitemap(
        [
            ("http://sample-site/list/detail-01.html", "2026-01-05"),
            ("http://sample-site/list/detail-02.html", "2026-02-20"),  # 日期不一致
            ("http://sample-site/list/detail-99.html", "2026-05-03"),  # sitemap 多出
        ]
    )
    diff = reconcile(CATALOG, sitemap)

    assert [row["url"] for row in diff["only_in_catalog"]] == [
        "http://sample-site/list/detail-03.html",
        "http://sample-site/list/detail-04.html",
    ]
    assert [row["url"] for row in diff["only_in_sitemap"]] == ["http://sample-site/list/detail-99.html"]
    assert len(diff["lastmod_mismatch"]) == 1
    assert diff["lastmod_mismatch"][0]["catalog_lastmod"] == "2026-02-04"
    assert diff["lastmod_mismatch"][0]["sitemap_lastmod"] == "2026-02-20"
    assert diff["counts"] == {
        "catalog": 4,
        "sitemap": 3,
        "only_in_catalog": 2,
        "only_in_sitemap": 1,
        "lastmod_mismatch": 1,
    }
    assert diff["verdict"] == "存在收录缺口"
    assert "2 条" in diff["conclusion"]


def test_only_in_catalog_carries_page_evidence():
    diff = reconcile(CATALOG, build_sitemap([("http://sample-site/list/detail-01.html", "2026-01-05")]))
    row = next(item for item in diff["only_in_catalog"] if item["url"].endswith("detail-04.html"))
    assert row["page"] == 4
    assert row["title"] == "收费清单"


def test_full_coverage_is_reported_as_complete():
    entries = [(item["url"], item["lastmod"]) for item in CATALOG]
    diff = reconcile(CATALOG, build_sitemap(entries))
    assert diff["counts"]["only_in_catalog"] == 0
    assert diff["counts"]["only_in_sitemap"] == 0
    assert diff["counts"]["lastmod_mismatch"] == 0
    assert diff["verdict"] == "收录完整"


def test_datetime_lastmod_compares_by_date_part_only():
    sitemap = build_sitemap(
        [
            ("http://sample-site/list/detail-01.html", "2026-01-05T09:30:00+08:00"),
            ("http://sample-site/list/detail-02.html", "2026-02-04"),
        ]
    )
    diff = reconcile(CATALOG[:2], sitemap)
    assert diff["lastmod_mismatch"] == []
    assert date_part("2026-01-05T09:30:00+08:00") == "2026-01-05"


def test_missing_lastmod_is_not_counted_as_mismatch():
    sitemap = parse_sitemap(
        '<?xml version="1.0"?><urlset><url><loc>http://sample-site/list/detail-01.html</loc></url></urlset>'
    )
    diff = reconcile(CATALOG[:1], sitemap)
    assert diff["lastmod_mismatch"] == []


def test_sitemap_loc_is_normalized_before_compare():
    raw = sitemap_xml(
        [
            ("http://SAMPLE-SITE:80/list/detail-01.html#top", "2026-01-05"),
            ("http://sample-site/list/detail-02.html?utm_source=x", "2026-02-04"),
        ]
    )
    sitemap = parse_sitemap(raw)
    assert set(sitemap) == {
        "http://sample-site/list/detail-01.html",
        "http://sample-site/list/detail-02.html",
    }
    diff = reconcile(CATALOG[:2], sitemap)
    assert diff["only_in_catalog"] == []


def test_invalid_xml_raises_value_error():
    with pytest.raises(ValueError):
        parse_sitemap("<urlset><url><loc>broken")


def test_sitemapindex_is_rejected_with_clear_message():
    index = '<?xml version="1.0"?><sitemapindex><sitemap><loc>http://x/s.xml</loc></sitemap></sitemapindex>'
    with pytest.raises(ValueError) as excinfo:
        parse_sitemap(index)
    assert "sitemapindex" in str(excinfo.value)
