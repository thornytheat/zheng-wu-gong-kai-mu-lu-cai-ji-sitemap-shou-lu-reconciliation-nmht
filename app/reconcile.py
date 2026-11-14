"""sitemap 对账：纯函数，目录集合 vs sitemap 集合的三类差异。"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from . import urls as urlmod


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def date_part(value: str | None) -> str:
    """把 lastmod 归一成 YYYY-MM-DD 以便比较。"""
    text = (value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def parse_sitemap(xml_text: str) -> dict[str, dict]:
    """解析 urlset，返回 {规范化 URL: {url, lastmod, lastmod_date}}。

    loc 无法规范化时跳过（会体现在对账的 only_in_catalog 里）；XML 非法时抛 ValueError。
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError("sitemap XML 解析失败：%s" % exc) from exc
    if _local(root.tag) == "sitemapindex":
        raise ValueError("暂不支持 sitemapindex（子 sitemap 索引）")
    found: dict[str, dict] = {}
    for node in root.iter():
        if _local(node.tag) != "url":
            continue
        loc = ""
        lastmod = ""
        for child in node:
            name = _local(child.tag)
            if name == "loc":
                loc = (child.text or "").strip()
            elif name == "lastmod":
                lastmod = (child.text or "").strip()
        if not loc:
            continue
        try:
            normalized = urlmod.normalize_url(loc)
        except ValueError:
            continue
        found[normalized] = {
            "url": normalized,
            "lastmod": lastmod,
            "lastmod_date": date_part(lastmod),
        }
    return found


def reconcile(catalog: list[dict], sitemap: dict[str, dict]) -> dict:
    catalog_map = {item["url"]: item for item in catalog}

    only_in_catalog = [
        {
            "url": url,
            "title": item.get("title", ""),
            "lastmod": date_part(item.get("lastmod", "")),
            "page": item.get("page", 0),
        }
        for url, item in catalog_map.items()
        if url not in sitemap
    ]
    only_in_sitemap = [
        {"url": url, "lastmod": entry.get("lastmod_date", "")}
        for url, entry in sitemap.items()
        if url not in catalog_map
    ]
    lastmod_mismatch = []
    for url, item in catalog_map.items():
        entry = sitemap.get(url)
        if not entry:
            continue
        catalog_date = date_part(item.get("lastmod", ""))
        sitemap_date = entry.get("lastmod_date", "")
        if catalog_date and sitemap_date and catalog_date != sitemap_date:
            lastmod_mismatch.append(
                {"url": url, "title": item.get("title", ""), "catalog_lastmod": catalog_date, "sitemap_lastmod": sitemap_date}
            )

    only_in_catalog.sort(key=lambda row: row["url"])
    only_in_sitemap.sort(key=lambda row: row["url"])
    lastmod_mismatch.sort(key=lambda row: row["url"])

    counts = {
        "catalog": len(catalog_map),
        "sitemap": len(sitemap),
        "only_in_catalog": len(only_in_catalog),
        "only_in_sitemap": len(only_in_sitemap),
        "lastmod_mismatch": len(lastmod_mismatch),
    }
    gap = counts["only_in_catalog"]
    mismatch = counts["lastmod_mismatch"]
    if gap == 0 and mismatch == 0:
        verdict = "收录完整"
        conclusion = "目录 %d 条全部进入 sitemap，lastmod 一致。" % counts["catalog"]
    else:
        verdict = "存在收录缺口"
        bits = []
        if gap:
            bits.append("目录多出 %d 条（未进入 sitemap）" % gap)
        if counts["only_in_sitemap"]:
            bits.append("sitemap 多出 %d 条（目录中不存在）" % counts["only_in_sitemap"])
        if mismatch:
            bits.append("%d 条 lastmod 与目录不一致" % mismatch)
        conclusion = "目录 %d 条、sitemap %d 条：" % (counts["catalog"], counts["sitemap"]) + "，".join(bits) + "。"

    return {
        "only_in_catalog": only_in_catalog,
        "only_in_sitemap": only_in_sitemap,
        "lastmod_mismatch": lastmod_mismatch,
        "counts": counts,
        "verdict": verdict,
        "conclusion": conclusion,
    }
