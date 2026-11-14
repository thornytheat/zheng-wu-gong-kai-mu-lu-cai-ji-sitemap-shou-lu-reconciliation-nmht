"""sitemap 抓取编排：递归读取 sitemapindex 与全部子文件，并逐文件登记状态。

设计要点：
- 任何单个子文件超时/损坏都不会让整次读取失败，状态写入文件明细；
- 只有全部文件都处于「成功/空文件」时 ``complete`` 才为真，调用方据此决定
  是发布最终差异还是只展示待核对范围；
- 抓取顺序固定（根文件 → 索引声明顺序的深度优先），配合 reconcile 的裁决规则，
  同一份 sitemap 重跑得到完全相同的合并结果。
"""

from __future__ import annotations

from . import reconcile
from .crawler import FetchResult, http_fetch

MAX_INDEX_DEPTH = 5


def _classify_fetch(result: FetchResult) -> tuple[str, str]:
    """把一次抓取结果映射成文件状态 + 说明。"""
    if result.error == "timeout":
        return reconcile.STATUS_TIMEOUT, "请求超时（已重试 1 次）"
    if result.error:
        return reconcile.STATUS_FETCH_ERROR, "抓取失败：%s" % result.error
    if result.status != 200:
        return reconcile.STATUS_HTTP_ERROR, "HTTP %s" % result.status
    return "", ""


def load_sitemaps(root_url: str, fetch=None) -> dict:
    """读取根 sitemap 及其全部子文件，返回汇总 manifest。

    manifest 结构::

        {
          "root_url": str, "complete": bool,
          "files": [文件明细, 按抓取顺序],
          "file_total": int, "unique_total": int, "raw_entry_total": int,
          "duplicate_total": int, "duplicate_urls": [...],
          "status_counts": {状态: 数量},
          "entries": {url: 合并后的条目（含出处 sources）},
          "error": str,  # 根文件就不可用时的整体错误说明
        }
    """
    fetch = fetch or http_fetch
    files: list[dict] = []
    queued: list[tuple[str, int]] = [(root_url, 0)]
    seen: set[str] = set()

    while queued:
        url, depth = queued.pop(0)
        if url in seen:
            # 索引间循环引用：登记一条未读取，不计入成功也不重复抓取
            files.append(
                {
                    "url": url,
                    "depth": depth,
                    "status": reconcile.STATUS_SKIPPED,
                    "detail": "该 sitemap 已在本次读取中出现，跳过循环引用",
                    "entry_count": 0,
                    "entries": [],
                    "kind": "",
                }
            )
            continue
        seen.add(url)

        result = fetch(url)
        record: dict = {"url": url, "depth": depth, "entry_count": 0, "entries": [], "kind": ""}
        bad_status, detail = _classify_fetch(result)
        if bad_status:
            record.update(status=bad_status, detail=detail)
            files.append(record)
            continue

        try:
            document = reconcile.parse_document(result.text)
        except ValueError as exc:
            record.update(status=reconcile.STATUS_PARSE_ERROR, detail=str(exc))
            files.append(record)
            continue

        if document["kind"] == "sitemapindex":
            record["kind"] = "sitemapindex"
            children = document["children"]
            record["child_count"] = len(children)
            if not children:
                record.update(status=reconcile.STATUS_EMPTY, detail="索引未声明任何子 sitemap")
            else:
                record.update(status=reconcile.STATUS_OK, detail="索引，含 %d 个子 sitemap" % len(children))
            files.append(record)
            if depth + 1 > MAX_INDEX_DEPTH:
                files.append(
                    {
                        "url": url,
                        "depth": depth + 1,
                        "status": reconcile.STATUS_SKIPPED,
                        "detail": "嵌套深度超过 %d 层，停止递归" % MAX_INDEX_DEPTH,
                        "entry_count": 0,
                        "entries": [],
                        "kind": "",
                    }
                )
                continue
            for child in children:
                queued.append((child["url"], depth + 1))
            continue

        record["kind"] = "urlset"
        entries = document["entries"]
        record["entries"] = entries
        record["entry_count"] = len(entries)
        if not entries:
            record.update(status=reconcile.STATUS_EMPTY, detail="urlset 中没有任何 url 条目")
        else:
            record.update(status=reconcile.STATUS_OK, detail="%d 条 url 条目" % len(entries))
        files.append(record)

    merged = reconcile.merge_sitemap_files(files)
    status_counts: dict[str, int] = {}
    for record in files:
        status_counts[record["status"]] = status_counts.get(record["status"], 0) + 1

    complete = all(record["status"] in reconcile.READABLE_STATUSES for record in files)
    manifest = {
        "root_url": root_url,
        "complete": complete,
        "files": files,
        "file_total": len(files),
        "urlset_total": sum(1 for record in files if record.get("kind") == "urlset"),
        "index_total": sum(1 for record in files if record.get("kind") == "sitemapindex"),
        "unique_total": merged["unique_total"],
        "raw_entry_total": merged["raw_entry_total"],
        "duplicate_total": len(merged["duplicate_urls"]),
        "duplicate_urls": merged["duplicate_urls"],
        "status_counts": status_counts,
        "entries": merged["entries"],
        "error": "",
    }
    if not complete:
        failed = [f for f in files if f["status"] not in reconcile.READABLE_STATUSES]
        manifest["error"] = "；".join("%s（%s：%s）" % (f["url"], f["status"], f.get("detail", "")) for f in failed)
    return manifest
