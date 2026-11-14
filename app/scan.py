"""采集 → 详情校验 → sitemap 对账的编排层（路由保持薄）。"""

from __future__ import annotations

from datetime import datetime

from . import crawler, reconcile, urls

EMPTY_DIFF = {
    "counts": {
        "catalog": 0,
        "sitemap": 0,
        "only_in_catalog": 0,
        "only_in_sitemap": 0,
        "lastmod_mismatch": 0,
    },
    "only_in_catalog": [],
    "only_in_sitemap": [],
    "lastmod_mismatch": [],
    "verdict": "未对账",
    "conclusion": "sitemap 不可用，本次未能完成对账。",
}


def run_scan(target: str, max_pages: int = 10, policy: str = "strict", fetch=None) -> dict:
    """执行一次完整扫描并返回可直接落库/渲染的结果字典。"""
    crawl = crawler.crawl(target, max_pages=max_pages, policy=policy, fetch=fetch)
    detail_failures = crawler.validate_catalog(crawl["catalog"], fetch=fetch)
    page_urls = {page["index"]: page["url"] for page in crawl["pagination"]["pages"]}
    failures = crawler.summarize_failures(
        detail_failures, crawl["offsite"], crawl["duplicate_pages"], page_urls
    )

    sitemap_url = urls.origin_of(crawl["target"]) + "/sitemap.xml"
    sitemap_map: dict[str, dict] = {}
    sitemap_error = ""
    try:
        result = (fetch or crawler.http_fetch)(sitemap_url)
        if result.error:
            sitemap_error = "sitemap 抓取失败：%s" % ("超时" if result.error == "timeout" else result.error)
        elif result.status != 200:
            sitemap_error = "sitemap 返回 HTTP %s" % result.status
        else:
            sitemap_map = reconcile.parse_sitemap(result.text)
    except ValueError as exc:
        sitemap_error = str(exc)

    diff = reconcile.reconcile(crawl["catalog"], sitemap_map) if not sitemap_error else dict(EMPTY_DIFF)
    if sitemap_error:
        diff["counts"]["catalog"] = len(crawl["catalog"])
        diff["conclusion"] = "目录 %d 条；%s。本次未做对账。" % (len(crawl["catalog"]), sitemap_error)

    pagination = crawl["pagination"]
    summary = {
        "pages_fetched": pagination["pages_fetched"],
        "stop_page": pagination["stop_page"],
        "stop_reason": pagination["reason"],
        "catalog_count": len(crawl["catalog"]),
        "sitemap_count": len(sitemap_map),
        "only_in_catalog": diff["counts"]["only_in_catalog"],
        "only_in_sitemap": diff["counts"]["only_in_sitemap"],
        "lastmod_mismatch": diff["counts"]["lastmod_mismatch"],
        "failures": failures["counts"],
        "verdict": diff["verdict"],
    }

    return {
        "scan_id": "",
        "target": crawl["target"],
        "target_host": crawl["target_host"],
        "max_pages": max_pages,
        "policy": crawl["policy"],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pagination": pagination,
        "catalog": {"count": len(crawl["catalog"]), "items": crawl["catalog"]},
        "sitemap": {
            "url": sitemap_url,
            "available": sitemap_error == "",
            "error": sitemap_error,
            "count": len(sitemap_map),
            "items": sorted(sitemap_map.values(), key=lambda row: row["url"]),
        },
        "diff": diff,
        "failures": failures,
        "normalization": {"examples": crawl["normalization_examples"]},
        "summary": summary,
    }
