"""分页边界与失败分类：重复页 / 空页 / 连续无新增 / 外站排除 / 抓取失败。"""

from __future__ import annotations

from app import crawler
from app.crawler import FetchResult

from conftest import FakeFetcher, entry, list_page

TARGET = "http://sample-site/list/page-1.html"
PAGE_URL = "http://sample-site/list/page-%d.html"


def build(pages: dict[int, list[tuple[str, str, str]]], **kwargs) -> FakeFetcher:
    html = {PAGE_URL % number: list_page(entries) for number, entries in pages.items()}
    return FakeFetcher(pages=html, **kwargs)


def page_urls(outcome: dict) -> dict[int, str]:
    return {page["index"]: page["url"] for page in outcome["pagination"]["pages"]}


def test_duplicate_page_stops_crawl_in_strict_mode():
    duplicate = [entry("detail-03", "2026-02-01"), entry("detail-04", "2026-02-02")]
    fetcher = build(
        {
            1: [entry("detail-01"), entry("detail-02")],
            2: duplicate,
            3: duplicate,  # 与第 2 页完全相同
            4: [entry("detail-05")],
        }
    )
    outcome = crawler.crawl(TARGET, max_pages=6, policy="strict", fetch=fetcher)

    assert "重复页" in outcome["pagination"]["reason"]
    assert outcome["pagination"]["stop_page"] == 3
    assert outcome["pagination"]["pages_fetched"] == 3
    assert outcome["duplicate_pages"] == [3]
    assert outcome["catalog_count"] == 4
    assert PAGE_URL % 4 not in fetcher.calls  # 第 4 页不应被抓取


def test_empty_page_stops_crawl():
    fetcher = build({1: [entry("detail-01"), entry("detail-02")], 2: []})
    outcome = crawler.crawl(TARGET, max_pages=6, policy="strict", fetch=fetcher)

    assert "空页" in outcome["pagination"]["reason"]
    assert outcome["pagination"]["stop_page"] == 2
    assert outcome["pagination"]["pages_fetched"] == 2
    assert outcome["catalog_count"] == 2


def test_two_consecutive_pages_without_new_urls_stops_crawl():
    a, b, c, d = entry("detail-01"), entry("detail-02"), entry("detail-03"), entry("detail-04")
    fetcher = build({1: [a, b, c], 2: [a, b], 3: [a, b, c], 4: [d]})
    outcome = crawler.crawl(TARGET, max_pages=6, policy="strict", fetch=fetcher)

    assert "连续 2 页无新增" in outcome["pagination"]["reason"]
    assert outcome["pagination"]["stop_page"] == 3
    assert outcome["catalog_count"] == 3
    assert PAGE_URL % 4 not in fetcher.calls


def test_tolerant_policy_records_duplicate_but_keeps_crawling():
    duplicate = [entry("detail-03"), entry("detail-04")]
    fetcher = build(
        {
            1: [entry("detail-01"), entry("detail-02")],
            2: duplicate,
            3: duplicate,
            4: [entry("detail-05")],
            5: [],
        }
    )
    outcome = crawler.crawl(TARGET, max_pages=6, policy="tolerant", fetch=fetcher)

    assert "空页" in outcome["pagination"]["reason"]
    assert outcome["pagination"]["stop_page"] == 5
    assert outcome["pagination"]["pages_fetched"] == 5
    assert outcome["duplicate_pages"] == [3]
    assert outcome["catalog_count"] == 5


def test_offsite_links_are_excluded_and_counted():
    fetcher = build(
        {
            1: [
                entry("detail-01"),
                ("https://example.com/news/1.html", "外站 1", "2026-05-04"),
                entry("detail-02"),
                ("https://other.example.net/gov/2.html", "外站 2", "2026-05-04"),
            ],
            2: [],
        }
    )
    outcome = crawler.crawl(TARGET, max_pages=6, policy="strict", fetch=fetcher)
    failures = crawler.summarize_failures({}, outcome["offsite"], outcome["duplicate_pages"], page_urls(outcome))

    assert outcome["catalog_count"] == 2
    assert {item["url"] for item in outcome["offsite"]} == {
        "https://example.com/news/1.html",
        "https://other.example.net/gov/2.html",
    }
    assert failures["counts"]["外站"] == 2
    assert len(failures["examples"]["外站"]) == 2


def test_missing_pager_stops_after_first_page():
    html = '<html><body><ul class="list">%s</ul></body></html>' % (
        '<li><a href="./detail-01.html">detail-01</a><time datetime="2026-01-05">2026-01-05</time></li>'
    )
    fetcher = FakeFetcher(pages={PAGE_URL % 1: html})
    outcome = crawler.crawl(TARGET, max_pages=6, policy="strict", fetch=fetcher)

    assert "分页链接" in outcome["pagination"]["reason"]
    assert outcome["pagination"]["pages_fetched"] == 1
    assert outcome["catalog_count"] == 1


def test_max_pages_limit_stops_crawl():
    fetcher = build({1: [entry("detail-01")], 2: [entry("detail-02")], 3: [entry("detail-03")]})
    outcome = crawler.crawl(TARGET, max_pages=2, policy="strict", fetch=fetcher)

    assert "最大页数" in outcome["pagination"]["reason"]
    assert outcome["pagination"]["stop_page"] == 2
    assert outcome["pagination"]["pages_fetched"] == 2


def test_list_page_timeout_stops_with_failure_reason():
    class TimeoutFetcher:
        def __call__(self, url, timeout=None, retries=None):
            return FetchResult(url, None, "", "timeout", 5000)

    outcome = crawler.crawl(TARGET, max_pages=6, policy="strict", fetch=TimeoutFetcher())
    assert "抓取失败" in outcome["pagination"]["reason"]
    assert "超时" in outcome["pagination"]["reason"]
    assert outcome["pagination"]["pages_fetched"] == 1


def test_detail_failures_are_classified():
    catalog = [
        {"url": "http://sample-site/list/detail-01.html", "page": 1},
        {"url": "http://sample-site/list/detail-02.html", "page": 1},
        {"url": "http://sample-site/list/detail-03.html", "page": 1},
        {"url": "http://sample-site/list/detail-04.html", "page": 1},
    ]

    class MixedFetcher:
        def __call__(self, url, timeout=None, retries=None):
            if url.endswith("detail-01.html"):
                return FetchResult(url, 404, "", None, 1)
            if url.endswith("detail-02.html"):
                return FetchResult(url, 503, "", None, 1)
            if url.endswith("detail-03.html"):
                return FetchResult(url, None, "", "timeout", 5000)
            return FetchResult(url, 200, "ok", None, 1)

    failures = crawler.validate_catalog(catalog, fetch=MixedFetcher())
    assert len(failures["4xx"]) == 1
    assert len(failures["5xx"]) == 1
    assert len(failures["超时"]) == 1
    assert failures["4xx"][0]["detail"] == "HTTP 404"


def test_tracking_duplicate_link_counts_once_per_page():
    fetcher = build(
        {
            1: [
                ("./detail-24.html", "detail-24", "2026-05-02"),
                ("./detail-24.html?utm_source=wechat#top", "detail-24 带参", "2026-05-02"),
            ],
            2: [],
        }
    )
    outcome = crawler.crawl(TARGET, max_pages=6, policy="strict", fetch=fetcher)
    assert outcome["catalog_count"] == 1
    assert len(outcome["normalization_examples"]) >= 1
