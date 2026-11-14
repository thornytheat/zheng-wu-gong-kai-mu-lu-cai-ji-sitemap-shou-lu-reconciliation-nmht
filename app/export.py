"""差异导出：对账三类差异 + 失败分类 → CSV 行。"""

from __future__ import annotations

import csv
import io

COLUMNS = ["差异类别", "URL", "标题", "目录日期", "sitemap日期", "说明"]


def build_rows(payload: dict) -> list[dict]:
    rows: list[dict] = []
    diff = payload.get("diff", {})
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
    for item in diff.get("only_in_sitemap", []):
        rows.append(
            {
                "差异类别": "only_in_sitemap（sitemap 多出）",
                "URL": item["url"],
                "标题": "",
                "目录日期": "",
                "sitemap日期": item.get("lastmod", ""),
                "说明": "sitemap 有该条，目录 6 页内未出现",
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
