"""sitemap 解析与对账：三类差异、日期口径、索引解析、跨文件合并裁决、读取不完整占位。"""

from __future__ import annotations

import pytest

from app.reconcile import (
    STATUS_EMPTY,
    STATUS_OK,
    STATUS_PARSE_ERROR,
    date_part,
    merge_sitemap_files,
    parse_document,
    parse_index,
    parse_sitemap,
    pending_diff,
    reconcile,
)

from conftest import empty_urlset_xml, sitemap_index_xml, sitemap_xml

CATALOG = [
    {"url": "http://sample-site/list/detail-01.html", "title": "预算公开", "lastmod": "2026-01-05", "page": 1},
    {"url": "http://sample-site/list/detail-02.html", "title": "招生意见", "lastmod": "2026-02-04", "page": 2},
    {"url": "http://sample-site/list/detail-03.html", "title": "处罚信息", "lastmod": "2026-02-05", "page": 2},
    {"url": "http://sample-site/list/detail-04.html", "title": "收费清单", "lastmod": "2026-04-02", "page": 4},
]

U1 = "http://sample-site/list/detail-01.html"
U2 = "http://sample-site/list/detail-02.html"
U3 = "http://sample-site/list/detail-03.html"
U4 = "http://sample-site/list/detail-04.html"
U99 = "http://sample-site/list/detail-99.html"


def build_sitemap(entries):
    return parse_sitemap(sitemap_xml(entries))


def test_three_diff_classes():
    sitemap = build_sitemap(
        [
            (U1, "2026-01-05"),
            (U2, "2026-02-20"),  # 日期不一致
            (U99, "2026-05-03"),  # sitemap 多出
        ]
    )
    diff = reconcile(CATALOG, sitemap)

    assert [row["url"] for row in diff["only_in_catalog"]] == [U3, U4]
    assert [row["url"] for row in diff["only_in_sitemap"]] == [U99]
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
    diff = reconcile(CATALOG, build_sitemap([(U1, "2026-01-05")]))
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
            (U1, "2026-01-05T09:30:00+08:00"),
            (U2, "2026-02-04"),
        ]
    )
    diff = reconcile(CATALOG[:2], sitemap)
    assert diff["lastmod_mismatch"] == []
    assert date_part("2026-01-05T09:30:00+08:00") == "2026-01-05"


def test_missing_lastmod_is_not_counted_as_mismatch():
    sitemap = parse_sitemap(
        '<?xml version="1.0"?><urlset><url><loc>%s</loc></url></urlset>' % U1
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
    assert set(sitemap) == {U1, U2}
    diff = reconcile(CATALOG[:2], sitemap)
    assert diff["only_in_catalog"] == []


def test_invalid_xml_raises_value_error():
    with pytest.raises(ValueError):
        parse_sitemap("<urlset><url><loc>broken")


def test_empty_document_is_parse_error_not_empty_file():
    with pytest.raises(ValueError):
        parse_document("")


# ── sitemapindex 解析 ──

def test_parse_document_distinguishes_index_and_urlset():
    index = parse_document(
        sitemap_index_xml([("http://sample-site/sitemap/a.xml", "2026-05-01")])
    )
    assert index["kind"] == "sitemapindex"
    assert index["children"] == [
        {"url": "http://sample-site/sitemap/a.xml", "lastmod": "2026-05-01", "lastmod_date": "2026-05-01"}
    ]

    urlset = parse_document(sitemap_xml([(U1, "2026-01-05")]))
    assert urlset["kind"] == "urlset"
    assert urlset["entries"][0]["url"] == U1


def test_parse_index_rejects_plain_urlset():
    with pytest.raises(ValueError):
        parse_index(sitemap_xml([(U1, "2026-01-05")]))


def test_parse_sitemap_rejects_index_with_clear_message():
    with pytest.raises(ValueError) as excinfo:
        parse_sitemap(sitemap_index_xml([("http://x/s.xml", "2026-05-01")]))
    assert "sitemapindex" in str(excinfo.value)


def test_empty_urlset_parses_as_zero_entries():
    document = parse_document(empty_urlset_xml())
    assert document["kind"] == "urlset"
    assert document["entries"] == []


# ── 跨文件合并：按 lastmod 裁决、保留出处 ──

def _file(name, entries, status=STATUS_OK):
    parsed = parse_document(sitemap_xml(entries))["entries"]
    return {"url": name, "status": status, "entries": parsed, "entry_count": len(parsed), "kind": "urlset"}


def test_merge_dedupes_and_keeps_every_source():
    files = [
        _file("http://x/a.xml", [(U1, "2026-01-01"), (U2, "2026-02-01")]),
        _file("http://x/b.xml", [(U1, "2026-03-01"), (U3, "2026-04-01")]),
    ]
    merged = merge_sitemap_files(files)

    assert merged["unique_total"] == 3
    assert merged["raw_entry_total"] == 4
    assert len(merged["duplicate_urls"]) == 1
    dup = merged["duplicate_urls"][0]
    assert dup["url"] == U1
    # b 文件的 lastmod 更新，裁决胜出
    assert dup["chosen_file"] == "http://x/b.xml"
    assert merged["entries"][U1]["lastmod_date"] == "2026-03-01"
    source_files = [s["file"] for s in merged["entries"][U1]["sources"]]
    assert source_files == ["http://x/a.xml", "http://x/b.xml"]
    selected = [s["file"] for s in merged["entries"][U1]["sources"] if s["selected"]]
    assert selected == ["http://x/b.xml"]


def test_merge_equal_lastmod_keeps_earlier_file_for_determinism():
    files = [
        _file("http://x/a.xml", [(U1, "2026-03-01")]),
        _file("http://x/b.xml", [(U1, "2026-03-01")]),
    ]
    merged = merge_sitemap_files(files)
    chosen = [s["file"] for s in merged["entries"][U1]["sources"] if s["selected"]]
    assert chosen == ["http://x/a.xml"]

    # 交换文件顺序后，胜出方随读取顺序变化；同一份 sitemap（顺序固定）重跑不变
    merged_again = merge_sitemap_files(list(reversed(files)))
    chosen_again = [s["file"] for s in merged_again["entries"][U1]["sources"] if s["selected"]]
    assert chosen_again == ["http://x/b.xml"]


def test_merge_compares_datetimes_across_date_and_timestamp_forms():
    files = [
        _file("http://x/a.xml", [(U1, "2026-03-01")]),
        _file("http://x/b.xml", [(U1, "2026-02-28T23:59:59+00:00")]),
    ]
    merged = merge_sitemap_files(files)
    # 3 月 1 日晚于 UTC 2 月 28 日，a 胜出
    assert merged["entries"][U1]["lastmod_date"] == "2026-03-01"


def test_merge_unparseable_lastmod_loses_to_real_date_but_beats_empty():
    files = [
        _file("http://x/a.xml", [(U1, "很久以前")]),
        _file("http://x/b.xml", [(U1, "2026-03-01")]),
        _file("http://x/c.xml", [(U2, "")]),
        _file("http://x/d.xml", [(U2, "手写日期")]),
    ]
    merged = merge_sitemap_files(files)
    assert merged["entries"][U1]["lastmod_date"] == "2026-03-01"
    assert merged["entries"][U2]["lastmod"] == "手写日期"


def test_merge_ignores_failed_files_but_reports_raw_total_of_readable_only():
    files = [
        _file("http://x/a.xml", [(U1, "2026-01-01")]),
        {"url": "http://x/b.xml", "status": STATUS_PARSE_ERROR, "entries": [], "entry_count": 0, "kind": ""},
        {"url": "http://x/c.xml", "status": STATUS_EMPTY, "entries": [], "entry_count": 0, "kind": "urlset"},
    ]
    merged = merge_sitemap_files(files)
    assert merged["unique_total"] == 1
    assert merged["raw_entry_total"] == 1


def test_merge_is_idempotent_across_runs():
    files = [
        _file("http://x/a.xml", [(U1, "2026-01-01"), (U2, "2026-02-01")]),
        _file("http://x/b.xml", [(U1, "2026-01-09"), (U3, "2026-04-01")]),
    ]
    first = merge_sitemap_files(files)
    second = merge_sitemap_files(files)
    assert first["unique_total"] == second["unique_total"]
    assert first["entries"][U1]["lastmod"] == second["entries"][U1]["lastmod"]
    assert [s["file"] for s in first["entries"][U1]["sources"]] == [
        s["file"] for s in second["entries"][U1]["sources"]
    ]


# ── 读取不完整：占位对账，绝不判收录完整 ──

def test_pending_diff_publishes_no_differences_and_never_says_complete():
    manifest = {
        "files": [
            {"url": "http://x/sitemap.xml", "status": STATUS_OK},
            {"url": "http://x/b.xml", "status": "超时"},
        ],
        "file_total": 2,
        "unique_total": 1,
    }
    diff = pending_diff(4, manifest)
    assert diff["verdict"] == "待核对"
    assert diff["counts"] == {
        "catalog": 4,
        "sitemap": 1,
        "only_in_catalog": 0,
        "only_in_sitemap": 0,
        "lastmod_mismatch": 0,
    }
    assert diff["only_in_catalog"] == []
    assert "不能判为收录完整" in diff["conclusion"]
    assert "超时" in diff["conclusion"]
