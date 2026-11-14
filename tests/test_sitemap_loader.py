"""sitemap 加载器：索引递归、成功/空文件/超时/HTTP失败/解析失败状态、循环引用、完整性判定。"""

from __future__ import annotations

from app.crawler import FetchResult
from app.reconcile import (
    STATUS_EMPTY,
    STATUS_HTTP_ERROR,
    STATUS_OK,
    STATUS_PARSE_ERROR,
    STATUS_SKIPPED,
    STATUS_TIMEOUT,
)
from app.sitemap_loader import load_sitemaps

from conftest import FakeFetcher, empty_urlset_xml, sitemap_index_xml, sitemap_xml

ROOT = "http://sample-site/sitemap.xml"
A = "http://sample-site/sitemap/a.xml"
B = "http://sample-site/sitemap/b.xml"
C = "http://sample-site/sitemap/c.xml"
U1 = "http://sample-site/list/detail-01.html"
U2 = "http://sample-site/list/detail-02.html"


def _index(children):
    return sitemap_index_xml([(url, "2026-05-01") for url in children])


def test_flat_urlset_still_works_as_single_file():
    fetcher = FakeFetcher(sitemap=sitemap_xml([(U1, "2026-01-01")]))
    manifest = load_sitemaps(ROOT, fetch=fetcher)

    assert manifest["complete"] is True
    assert manifest["file_total"] == 1
    assert manifest["urlset_total"] == 1
    assert manifest["unique_total"] == 1
    assert manifest["files"][0]["status"] == STATUS_OK
    assert manifest["status_counts"] == {STATUS_OK: 1}


def test_index_recurses_into_all_children_and_merges():
    fetcher = FakeFetcher(
        sitemap=_index([A, B, C]),
        sitemap_files={
            A: sitemap_xml([(U1, "2026-01-01")]),
            B: sitemap_xml([(U1, "2026-02-01"), (U2, "2026-03-01")]),
            C: empty_urlset_xml(),
        },
    )
    manifest = load_sitemaps(ROOT, fetch=fetcher)

    assert [f["url"] for f in manifest["files"]] == [ROOT, A, B, C]
    assert manifest["complete"] is True
    assert manifest["index_total"] == 1
    assert manifest["urlset_total"] == 3
    assert manifest["unique_total"] == 2
    # 原始条目 3（a 1 + b 2 + c 0）＝ 去重 2 ＋ 重复占用 1
    assert manifest["raw_entry_total"] == 3
    assert manifest["raw_entry_total"] - manifest["unique_total"] == 1
    assert manifest["duplicate_total"] == 1
    assert manifest["duplicate_urls"][0]["chosen_file"] == B
    assert manifest["files"][-1]["status"] == STATUS_EMPTY
    assert manifest["status_counts"][STATUS_EMPTY] == 1


def test_timeout_child_marks_incomplete_but_keeps_other_files():
    fetcher = FakeFetcher(
        sitemap=_index([A, B]),
        sitemap_files={A: sitemap_xml([(U1, "2026-01-01")])},
        sitemap_errors={B: FetchResult(B, None, "", "timeout", 5001)},
    )
    manifest = load_sitemaps(ROOT, fetch=fetcher)

    assert manifest["complete"] is False
    statuses = {f["url"]: f["status"] for f in manifest["files"]}
    assert statuses[A] == STATUS_OK
    assert statuses[B] == STATUS_TIMEOUT
    # 已读部分仍参与汇总，可用于展示待核对范围
    assert manifest["unique_total"] == 1
    assert STATUS_TIMEOUT in manifest["error"]


def test_http_error_and_parse_error_are_distinct_statuses():
    fetcher = FakeFetcher(
        sitemap=_index([A, B, C]),
        sitemap_files={
            A: sitemap_xml([(U1, "2026-01-01")]),
            C: "<urlset><url><loc>broken",
        },
        sitemap_errors={B: FetchResult(B, 503, "", None, 2)},
    )
    manifest = load_sitemaps(ROOT, fetch=fetcher)

    assert manifest["complete"] is False
    statuses = {f["url"]: f["status"] for f in manifest["files"]}
    assert statuses[B] == STATUS_HTTP_ERROR
    assert statuses[C] == STATUS_PARSE_ERROR
    assert set(manifest["status_counts"]) >= {STATUS_OK, STATUS_HTTP_ERROR, STATUS_PARSE_ERROR}


def test_root_fetch_failure_is_recorded_as_single_failed_file():
    fetcher = FakeFetcher(sitemap_errors={ROOT: FetchResult(ROOT, None, "", "timeout", 5000)})
    manifest = load_sitemaps(ROOT, fetch=fetcher)

    assert manifest["complete"] is False
    assert manifest["file_total"] == 1
    assert manifest["files"][0]["status"] == STATUS_TIMEOUT
    assert manifest["unique_total"] == 0


def test_root_parse_error_does_not_abort_everything():
    fetcher = FakeFetcher(sitemap="<?xml not valid at all")
    manifest = load_sitemaps(ROOT, fetch=fetcher)

    assert manifest["complete"] is False
    assert manifest["files"][0]["status"] == STATUS_PARSE_ERROR


def test_nested_index_is_followed_depth_first():
    nested = "http://sample-site/sitemap/nested.xml"
    fetcher = FakeFetcher(
        sitemap=_index([A, nested]),
        sitemap_files={
            A: sitemap_xml([(U1, "2026-01-01")]),
            nested: _index([B]),
            B: sitemap_xml([(U2, "2026-02-01")]),
        },
    )
    manifest = load_sitemaps(ROOT, fetch=fetcher)

    assert manifest["complete"] is True
    assert [f["url"] for f in manifest["files"]] == [ROOT, A, nested, B]
    assert manifest["index_total"] == 2
    assert manifest["unique_total"] == 2


def test_circular_index_reference_is_skipped_not_infinite():
    self_index = "http://sample-site/sitemap/loop.xml"
    fetcher = FakeFetcher(
        sitemap=_index([self_index]),
        sitemap_files={self_index: _index([ROOT])},  # ROOT 指回已读文件
    )
    manifest = load_sitemaps(ROOT, fetch=fetcher)

    statuses = [f["status"] for f in manifest["files"]]
    assert STATUS_SKIPPED in statuses
    assert manifest["complete"] is False
    assert len(manifest["files"]) == 3  # ROOT、loop、指回 ROOT 的跳过记录


def test_rerun_same_sitemap_produces_identical_manifest():
    def build():
        fetcher = FakeFetcher(
            sitemap=_index([A, B]),
            sitemap_files={
                A: sitemap_xml([(U1, "2026-01-01"), (U2, "2026-02-01")]),
                B: sitemap_xml([(U1, "2026-01-01")]),
            },
        )
        return load_sitemaps(ROOT, fetch=fetcher)

    first, second = build(), build()
    assert [(f["url"], f["status"], f["entry_count"]) for f in first["files"]] == [
        (f["url"], f["status"], f["entry_count"]) for f in second["files"]
    ]
    assert first["unique_total"] == second["unique_total"]
    assert first["duplicate_urls"] == second["duplicate_urls"]
