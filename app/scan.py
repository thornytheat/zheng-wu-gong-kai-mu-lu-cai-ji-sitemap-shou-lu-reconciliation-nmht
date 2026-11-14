"""采集 → 详情校验 → sitemap 对账的编排层（路由保持薄）。"""

from __future__ import annotations

from datetime import datetime

from . import crawler, reconcile, sitemap_loader, urls


def _empty_manifest(sitemap_url: str, status: str, detail: str) -> dict:
    """根 sitemap 就无法读取时的最小 manifest：一个文件、读取不完整。"""
    return {
        "root_url": sitemap_url,
        "complete": False,
        "files": [
            {
                "url": sitemap_url,
                "depth": 0,
                "status": status,
                "detail": detail,
                "entry_count": 0,
                "entries": [],
                "kind": "",
            }
        ],
        "file_total": 1,
        "urlset_total": 0,
        "index_total": 0,
        "unique_total": 0,
        "raw_entry_total": 0,
        "duplicate_total": 0,
        "duplicate_urls": [],
        "status_counts": {status: 1},
        "entries": {},
        "error": "%s（%s：%s）" % (sitemap_url, status, detail),
    }


def run_scan(target: str, max_pages: int = 10, policy: str = "strict", fetch=None) -> dict:
    """执行一次完整扫描并返回可直接落库/渲染的结果字典。"""
    fetch = fetch or crawler.http_fetch
    crawl = crawler.crawl(target, max_pages=max_pages, policy=policy, fetch=fetch)
    detail_failures = crawler.validate_catalog(crawl["catalog"], fetch=fetch)
    page_urls = {page["index"]: page["url"] for page in crawl["pagination"]["pages"]}
    failures = crawler.summarize_failures(
        detail_failures, crawl["offsite"], crawl["duplicate_pages"], page_urls
    )

    sitemap_url = urls.origin_of(crawl["target"]) + "/sitemap.xml"
    try:
        manifest = sitemap_loader.load_sitemaps(sitemap_url, fetch=fetch)
    except Exception as exc:  # noqa: BLE001 - 加载器自身异常也要落为「待核对」而非 500
        manifest = _empty_manifest(sitemap_url, reconcile.STATUS_FETCH_ERROR, str(exc))

    # 合并后的 sitemap 条目（含出处），仅在全部文件读取成功时用于最终对账
    sitemap_entries = manifest["entries"]
    if manifest["complete"]:
        diff = reconcile.reconcile(crawl["catalog"], sitemap_entries)
    else:
        diff = reconcile.pending_diff(len(crawl["catalog"]), manifest)

    # 恒等式：各文件原始条目数 = 去重后 URL 数 + 跨文件/文件内重复占用的条目数
    overlap_count = manifest["raw_entry_total"] - manifest["unique_total"]
    manifest["overlap_count"] = overlap_count
    manifest["arithmetic_ok"] = (
        manifest["raw_entry_total"] == manifest["unique_total"] + overlap_count
        and manifest["duplicate_total"] <= manifest["unique_total"]
    )

    sitemap_items = sorted(
        (
            {
                "url": url,
                "lastmod": entry.get("lastmod", ""),
                "lastmod_date": entry.get("lastmod_date", ""),
                "sources": entry.get("sources", []),
            }
            for url, entry in sitemap_entries.items()
        ),
        key=lambda row: row["url"],
    )

    pagination = crawl["pagination"]
    summary = {
        "pages_fetched": pagination["pages_fetched"],
        "stop_page": pagination["stop_page"],
        "stop_reason": pagination["reason"],
        "catalog_count": len(crawl["catalog"]),
        "sitemap_count": manifest["unique_total"],
        "sitemap_files": manifest["file_total"],
        "sitemap_complete": manifest["complete"],
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
            "available": manifest["complete"],
            "complete": manifest["complete"],
            "error": manifest["error"],
            "count": manifest["unique_total"],
            "raw_entry_total": manifest["raw_entry_total"],
            "file_total": manifest["file_total"],
            "urlset_total": manifest["urlset_total"],
            "index_total": manifest["index_total"],
            "duplicate_total": manifest["duplicate_total"],
            "overlap_count": overlap_count,
            "arithmetic_ok": manifest["arithmetic_ok"],
            "status_counts": manifest["status_counts"],
            "files": manifest["files"],
            "duplicate_urls": manifest["duplicate_urls"],
            "items": sitemap_items,
        },
        "diff": diff,
        "failures": failures,
        "normalization": {"examples": crawl["normalization_examples"]},
        "summary": summary,
    }
