"""差异导出：对账三类差异 + 失败分类 → CSV 行。"""

from __future__ import annotations

import csv
import io

COLUMNS = ["差异类别", "URL", "标题", "目录日期", "sitemap日期", "说明"]


def build_rows(payload: dict) -> list[dict]:
    rows: list[dict] = []
    sitemap = payload.get("sitemap", {})
    diff = payload.get("diff", {})

    if not sitemap.get("complete", False):
        # 读取不完整：只导出待核对范围与文件状态，不导出任何差异结论
        for index, file in enumerate(sitemap.get("files", []), start=1):
            rows.append(
                {
                    "差异类别": "sitemap文件：%s" % file.get("status", ""),
                    "URL": file.get("url", ""),
                    "标题": "",
                    "目录日期": "",
                    "sitemap日期": "",
                    "说明": "第 %d 个文件，%s；读取不完整，本次不发布差异" % (index, file.get("detail", "")),
                }
            )
        rows.append(
            {
                "差异类别": "待核对范围",
                "URL": "",
                "标题": "",
                "目录日期": "",
                "sitemap日期": "",
                "说明": "目录 %d 条待核对；已读 sitemap 去重 %d 条；全部文件读取成功后重跑才发布最终差异"
                % (payload.get("catalog", {}).get("count", 0), sitemap.get("count", 0)),
            }
        )
        return rows

    for item in diff.get("only_in_catalog", []):
        rows.append(
            {
                "差异类别": "only_in_catalog（目录有、sitemap 缺）",
                "URL": item["url"],
                "标题": item.get("title", ""),
                "目录日期": item.get("lastmod", ""),
                "sitemap日期": "",
                "说明": "目录第 %s 页出现，sitemap 未收录" % item.get("page", ""),
            }
        )
    source_by_url = {item["url"]: item.get("sources", []) for item in sitemap.get("items", [])}
    for item in diff.get("only_in_sitemap", []):
        sources = source_by_url.get(item["url"], [])
        files = "、".join(src["file"] for src in sources) or "未知文件"
        rows.append(
            {
                "差异类别": "only_in_sitemap（sitemap 多出）",
                "URL": item["url"],
                "标题": "",
                "目录日期": "",
                "sitemap日期": item.get("lastmod", ""),
                "说明": "sitemap 有该条，目录 6 页内未出现；出处：%s" % files,
            }
        )
    for item in diff.get("lastmod_mismatch", []):
        rows.append(
            {
                "差异类别": "lastmod_mismatch（日期不一致）",
                "URL": item["url"],
                "标题": item.get("title", ""),
                "目录日期": item.get("catalog_lastmod", ""),
                "sitemap日期": item.get("sitemap_lastmod", ""),
                "说明": "目录发布日期与 sitemap lastmod 不同",
            }
        )
    for category, items in payload.get("failures", {}).get("items", {}).items():
        for item in items:
            rows.append(
                {
                    "差异类别": "失败：%s" % category,
                    "URL": item.get("url", ""),
                    "标题": "",
                    "目录日期": "",
                    "sitemap日期": "",
                    "说明": item.get("detail", ""),
                }
            )
    return rows


def to_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    buffer.write("\ufeff")  # 便于 Excel 直接打开中文
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()
