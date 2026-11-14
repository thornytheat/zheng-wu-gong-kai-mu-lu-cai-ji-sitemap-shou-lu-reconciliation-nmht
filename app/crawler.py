"""采集执行与分页诊断：顺序抓列表页、同站过滤、终止判定、失败分类。

外部依赖只有标准库 urllib；抓取函数可在测试中注入（fetch 参数）。
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

from . import settings
from . import urls as urlmod

USER_AGENT = "crawlscope/1.0 (+gov-site-catalog-audit)"
FAILURE_CATEGORIES = ("4xx", "5xx", "超时", "外站", "重复页")


@dataclass
class FetchResult:
    url: str
    status: int | None
    text: str
    error: str | None = None
    elapsed_ms: int = 0


def http_fetch(url: str, timeout: float | None = None, retries: int | None = None) -> FetchResult:
    """带超时与重试 1 次的 GET；4xx 不重试，5xx/超时重试一次。"""
    timeout = settings.FETCH_TIMEOUT if timeout is None else timeout
    retries = settings.FETCH_RETRIES if retries is None else retries
    last = FetchResult(url=url, status=None, text="", error="error")
    for attempt in range(retries + 1):
        started = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                elapsed = int((time.monotonic() - started) * 1000)
                return FetchResult(url, response.status, raw.decode(charset, "replace"), None, elapsed)
        except urllib.error.HTTPError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - 响应体不可读时忽略
                body = ""
            last = FetchResult(url, exc.code, body, None, elapsed)
            if exc.code < 500:
                return last
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            reason = str(getattr(exc, "reason", exc))
            is_timeout = isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in reason.lower()
            last = FetchResult(url, None, "", "timeout" if is_timeout else "error", elapsed)
        if attempt < retries:
            time.sleep(0.05)
    return last


class _ListParser(HTMLParser):
    """提取列表条目（li > a + time）与分页条（a[data-page]）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self.pager: list[tuple[int, str]] = []
        self._li: dict | None = None
        self._in_a = False
        self._in_time = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: (value or "") for key, value in attrs}
        if tag == "li":
            self._li = {"href": None, "title": [], "lastmod": None}
        elif tag == "a":
            href = attr.get("href", "")
            page = attr.get("data-page", "")
            if page.isdigit():
                self.pager.append((int(page), href))
            if self._li is not None and self._li["href"] is None and href:
                self._li["href"] = href
                self._in_a = True
        elif tag == "time" and self._li is not None:
            self._in_time = True
            self._li["lastmod"] = attr.get("datetime") or None

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_a = False
        elif tag == "time":
            self._in_time = False
        elif tag == "li" and self._li is not None:
            if self._li["href"]:
                self._li["title"] = "".join(self._li["title"]).strip()
                self.items.append(self._li)
            self._li = None

    def handle_data(self, data: str) -> None:
        if self._li is not None and self._in_a and not self._in_time:
            self._li["title"].append(data)


def _parse(html: str) -> _ListParser:
    parser = _ListParser()
    parser.feed(html or "")
    parser.close()
    return parser


def extract_page_links(html: str, base_url: str) -> dict[int, str]:
    """分页条 → {页码: 规范化 URL}；无效链接跳过。"""
    out: dict[int, str] = {}
    for number, href in _parse(html).pager:
        try:
            out.setdefault(number, urlmod.normalize_url(href, base_url))
        except ValueError:
            continue
    return out


def extract_entries(html: str, base_url: str) -> list[dict]:
    """列表页条目 → [{raw_href, url, title, lastmod, changes}]；链接不可规范化时跳过。"""
    entries: list[dict] = []
    for item in _parse(html).items:
        raw = item["href"]
        try:
            url, changes = urlmod.normalize_with_evidence(raw, base_url)
        except ValueError:
            continue
        entries.append(
            {
                "raw_href": raw,
                "url": url,
                "title": item["title"],
                "lastmod": item["lastmod"] or "",
                "changes": changes,
            }
        )
    return entries


def _empty_failures() -> dict[str, list[dict]]:
    return {category: [] for category in FAILURE_CATEGORIES}


def crawl(target: str, max_pages: int = 10, policy: str = "strict", fetch=None) -> dict:
    """顺序抓取列表页并判定分页边界。

    终止条件（按判定顺序）：
    1. 本页提取条目为 0（空页）
    2. 本页同站 URL 集合与上一页完全相同（重复页；严格模式立即停止）
    3. 连续 2 页无新增 URL
    另加：达到最大页数 / 分页链接缺失 / 页面抓取失败。
    """
    fetch = fetch or http_fetch
    policy = policy if policy in settings.POLICIES else "strict"
    entry_url = urlmod.require_target(target)
    target_host = urlmod.host_of(entry_url)

    pages: list[dict] = []
    catalog: dict[str, dict] = {}
    offsite: list[dict] = []
    duplicate_pages: list[int] = []
    examples: list[dict] = []
    pager: dict[int, str] = {}
    seen_pages: set[str] = set()
    prev_set: set[str] | None = None
    no_new_streak = 0
    stop_reason = ""
    stop_page = 0
    index = 1

    while index <= max_pages:
        current = entry_url if index == 1 else pager.get(index)
        if not current:
            stop_reason = "第 %d 页分页链接缺失（分页条只到第 %d 页）" % (index, index - 1)
            stop_page = index - 1
            break
        if current in seen_pages:
            stop_reason = "第 %d 页分页链接指回已抓取页（%s）" % (index, current)
            stop_page = index - 1
            break
        seen_pages.add(current)

        result = fetch(current)
        page = {
            "index": index,
            "url": current,
            "status": result.status,
            "entries": 0,
            "new": 0,
            "duplicate": False,
            "note": "",
            "elapsed_ms": result.elapsed_ms,
        }

        if result.error or result.status is None or (result.status and result.status >= 400):
            detail = "超时（已重试 1 次）" if result.error == "timeout" else (result.error or "HTTP %s" % result.status)
            page["note"] = "列表页抓取失败：%s" % detail
            pages.append(page)
            stop_page = index
            stop_reason = "第 %d 页列表页抓取失败：%s" % (index, detail)
            break

        before = set(catalog)
        current_set: set[str] = set()
        for entry in extract_entries(result.text, current):
            if not urlmod.is_same_site(entry["url"], target_host):
                offsite.append({"url": entry["url"], "page": index, "raw_href": entry["raw_href"]})
                continue
            current_set.add(entry["url"])
            if entry["url"] not in catalog:
                catalog[entry["url"]] = {
                    "url": entry["url"],
                    "title": entry["title"],
                    "lastmod": entry["lastmod"],
                    "page": index,
                    "raw_href": entry["raw_href"],
                }
            if entry["changes"]:
                examples.append(
                    {
                        "raw": entry["raw_href"],
                        "url": entry["url"],
                        "changes": entry["changes"],
                        "page": index,
                        "same_site": True,
                    }
                )

        page["entries"] = len(current_set)
        page["new"] = len(current_set - before)

        if page["entries"] == 0:
            page["note"] = "空列表页：提取条目为 0"
            stop_reason = "空页（本页提取条目为 0）"
        elif index > 1 and current_set == prev_set:
            page["duplicate"] = True
            duplicate_pages.append(index)
            if policy == "strict":
                page["note"] = "本页同站 URL 集合与第 %d 页完全相同" % (index - 1)
                stop_reason = "重复页（本页 URL 集合与上一页完全相同）"
            else:
                page["note"] = "重复页（容忍模式：记录后继续）"

        if not stop_reason and page["new"] == 0:
            no_new_streak += 1
            if no_new_streak >= 2:
                stop_reason = "连续 2 页无新增 URL（第 %d-%d 页）" % (index - 1, index)
        elif page["new"] > 0:
            no_new_streak = 0

        if not stop_reason and index == max_pages:
            stop_reason = "达到最大页数（%d 页）" % max_pages

        pages.append(page)
        if stop_reason:
            stop_page = index
            break

        prev_set = current_set
        links = extract_page_links(result.text, current)
        if links:
            pager.update(links)
        if index == 1 and not any(number >= 2 for number in pager):
            stop_reason = "未发现第 2 页分页链接（只抓到列表第 1 页）"
            stop_page = 1
            break
        index += 1

    return {
        "target": entry_url,
        "target_host": target_host,
        "policy": policy,
        "max_pages": max_pages,
        "pagination": {
            "pages_fetched": len(pages),
            "stop_page": stop_page,
            "reason": stop_reason,
            "pages": pages,
        },
        "catalog": list(catalog.values()),
        "catalog_count": len(catalog),
        "offsite": offsite,
        "duplicate_pages": duplicate_pages,
        "normalization_examples": sorted(examples, key=lambda e: (-len(e["changes"]), e["page"]))[:12],
    }


def validate_catalog(catalog: list[dict], fetch=None) -> dict[str, list[dict]]:
    """逐条访问目录详情 URL，产出失败分类（4xx / 5xx / 超时）。"""
    fetch = fetch or http_fetch
    failures = _empty_failures()
    for item in catalog:
        result = fetch(item["url"])
        if result.error == "timeout":
            failures["超时"].append({"url": item["url"], "detail": "请求超时（重试 1 次后仍失败）", "page": item["page"]})
        elif result.error:
            failures["超时"].append({"url": item["url"], "detail": "连接失败：%s" % result.error, "page": item["page"]})
        elif result.status and 400 <= result.status < 500:
            failures["4xx"].append({"url": item["url"], "detail": "HTTP %d" % result.status, "page": item["page"]})
        elif result.status and result.status >= 500:
            failures["5xx"].append({"url": item["url"], "detail": "HTTP %d" % result.status, "page": item["page"]})
    return failures


def summarize_failures(failures: dict[str, list[dict]], offsite: list[dict], duplicate_pages: list[int], page_urls: dict[int, str]) -> dict:
    """合并分类并按类别给计数 + 至多 3 条示例 URL。"""
    merged: dict[str, list[dict]] = {category: list(failures.get(category, [])) for category in FAILURE_CATEGORIES}
    for item in offsite:
        merged["外站"].append({"url": item["url"], "detail": "host 与目标站不同，已从目录剔除", "page": item["page"]})
    for number in duplicate_pages:
        merged["重复页"].append(
            {
                "url": page_urls.get(number, "第 %d 页" % number),
                "detail": "该页同站 URL 集合与上一页完全相同",
                "page": number,
            }
        )
    return {
        "counts": {category: len(items) for category, items in merged.items()},
        "items": merged,
        "examples": {category: [item["url"] for item in items[:3]] for category, items in merged.items()},
    }
