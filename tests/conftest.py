"""测试共用：可注入的假站点，避免单测依赖网络。"""

from __future__ import annotations

import pytest

from app.crawler import FetchResult


def pager_html(total: int = 6) -> str:
    anchors = "".join(
        '<a data-page="%d" href="./page-%d.html">%d</a>' % (number, number, number) for number in range(1, total + 1)
    )
    return '<nav class="pager">%s</nav>' % anchors


def list_page(entries: list[tuple[str, str, str]], total: int = 6) -> str:
    items = "".join(
        '<li class="item"><a href="%s">%s</a><time datetime="%s">%s</time></li>' % (href, title, date, date)
        for href, title, date in entries
    )
    return '<html><body><ul class="list">%s</ul>%s</body></html>' % (items, pager_html(total))


def entry(name: str, date: str = "2026-01-05") -> tuple[str, str, str]:
    return ("./%s.html" % name, name, date)


def sitemap_xml(entries: list[tuple[str, str]]) -> str:
    body = "".join("<url><loc>%s</loc><lastmod>%s</lastmod></url>" % (url, date) for url, date in entries)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>' % body
    )


def sitemap_index_xml(children: list[tuple[str, str]]) -> str:
    body = "".join(
        "<sitemap><loc>%s</loc><lastmod>%s</lastmod></sitemap>" % (url, date) for url, date in children
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</sitemapindex>' % body
    )


def empty_urlset_xml() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'


class FakeFetcher:
    """把 URL 映射成固定响应；未登记的 URL 一律 404。

    sitemap 相关约定：
    - ``sitemap`` 作为根 /sitemap.xml 的响应（可以是 urlset 或 sitemapindex）；
    - ``sitemap_files`` 映射子 sitemap 的完整 URL → 响应文本；
    - ``sitemap_errors`` 映射 sitemap URL → FetchResult（模拟超时/HTTP 失败）。
    """

    def __init__(
        self,
        pages: dict[str, str] | None = None,
        statuses: dict[str, tuple[int, str]] | None = None,
        sitemap: str | None = None,
        sitemap_files: dict[str, str] | None = None,
        sitemap_errors: dict | None = None,
    ):
        self.pages = pages or {}
        self.statuses = statuses or {}
        self.sitemap = sitemap
        self.sitemap_files = sitemap_files or {}
        self.sitemap_errors = sitemap_errors or {}
        self.calls: list[str] = []

    def __call__(self, url: str, timeout=None, retries=None) -> FetchResult:
        self.calls.append(url)
        if url in self.pages:
            return FetchResult(url, 200, self.pages[url], None, 1)
        if url in self.statuses:
            status, text = self.statuses[url]
            return FetchResult(url, status, text, None, 1)
        if url in self.sitemap_errors:
            return self.sitemap_errors[url]
        if url in self.sitemap_files:
            return FetchResult(url, 200, self.sitemap_files[url], None, 1)
        if url.endswith("/sitemap.xml"):
            if self.sitemap is None:
                return FetchResult(url, 404, "", None, 1)
            return FetchResult(url, 200, self.sitemap, None, 1)
        return FetchResult(url, 404, "", None, 1)


@pytest.fixture()
def temp_db(monkeypatch, tmp_path):
    path = tmp_path / "crawlscope.db"
    monkeypatch.setenv("DB_PATH", str(path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return path
