"""sitemap 解析与对账（纯函数）。

支持两种 sitemap 文档：
- ``urlset``：普通 URL 清单；
- ``sitemapindex``：子 sitemap 索引，解析出子文件清单后由 sitemap_loader 递归抓取。

跨文件合并规则见 :func:`merge_sitemap_files`：同一规范化 URL 出现在多个文件时，
按 lastmod 更新时间裁决，全部出处都保留在 ``sources`` 里。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

from . import urls as urlmod

# 单个 sitemap 文件的读取状态
STATUS_OK = "成功"
STATUS_EMPTY = "空文件"
STATUS_TIMEOUT = "超时"
STATUS_HTTP_ERROR = "HTTP失败"
STATUS_PARSE_ERROR = "解析失败"
STATUS_FETCH_ERROR = "抓取失败"
# 索引声明了子文件，但因递归深度/循环未继续读取
STATUS_SKIPPED = "未读取"

READABLE_STATUSES = (STATUS_OK, STATUS_EMPTY)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def date_part(value: str | None) -> str:
    """把 lastmod 归一成 YYYY-MM-DD 以便比较。"""
    text = (value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def _lastmod_sort_key(lastmod: str) -> tuple[int, object]:
    """lastmod 新旧裁决键：越新越大。

    分层口径：
    1. 能解析成日期/时间戳的，按真实时间先后比较（带时区的转 UTC）；
    2. 解析不了但非空的原值，退化为字符串比较，排在可解析日期之后；
    3. 空 lastmod 最旧。
    """
    text = (lastmod or "").strip()
    if not text:
        return (0, 0)
    parsed = _parse_datetime(text)
    if parsed is not None:
        return (2, parsed)
    return (1, text)


def _parse_datetime(text: str):
    candidate = text.replace("Z", "+00:00")
    try:
        value = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    if value.tzinfo is not None:
        # 带时区的时间统一转 UTC 朴素时间，裁决不依赖运行机时区
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def parse_document(xml_text: str) -> dict:
    """解析任意 sitemap 文档，返回 ``{"kind": "urlset"|"sitemapindex", ...}``。

    - urlset → ``{"kind": "urlset", "entries": [{url, lastmod, lastmod_date}]}``，
      loc 无法规范化的条目跳过；
    - sitemapindex → ``{"kind": "sitemapindex", "children": [{url, lastmod}]}``；
    - XML 非法（含空文档）抛 ValueError，由调用方记为「解析失败」。
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError("sitemap XML 解析失败：%s" % exc) from exc
    kind = _local(root.tag)
    if kind == "sitemapindex":
        children: list[dict] = []
        for node in root.iter():
            if _local(node.tag) != "sitemap":
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
            children.append({"url": normalized, "lastmod": lastmod, "lastmod_date": date_part(lastmod)})
        return {"kind": "sitemapindex", "children": children}

    entries: list[dict] = []
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
        entries.append({"url": normalized, "lastmod": lastmod, "lastmod_date": date_part(lastmod)})
    return {"kind": "urlset", "entries": entries}


def parse_index(xml_text: str) -> list[dict]:
    """只解析 sitemapindex，返回子文件清单；非索引文档抛 ValueError。"""
    document = parse_document(xml_text)
    if document["kind"] != "sitemapindex":
        raise ValueError("该文档不是 sitemapindex")
    return document["children"]


def parse_sitemap(xml_text: str) -> dict[str, dict]:
    """解析 urlset，返回 {规范化 URL: {url, lastmod, lastmod_date}}。

    兼容旧调用：sitemapindex 与非法 XML 都抛 ValueError。
    """
    document = parse_document(xml_text)
    if document["kind"] == "sitemapindex":
        raise ValueError("该地址是 sitemapindex（子 sitemap 索引），请使用 load_sitemaps 递归读取")
    return {entry["url"]: dict(entry) for entry in document["entries"]}


def merge_sitemap_files(files: list[dict]) -> dict:
    """把多个 urlset 文件的条目合并去重，按 lastmod 裁决并保留出处。

    入参 ``files`` 为文件明细列表（读取顺序即裁决的稳定次序），每个文件含：
    ``url``、``status``、``entries``（成功/空文件时为条目列表）。

    同一规范化 URL 出现在多个文件时：
    - lastmod 可解析/更新的一方胜出；lastmod 完全相同则取读取顺序更早的文件，
      保证同一份 sitemap 重跑结果不变；
    - 每个出现位置都记入 ``sources``，胜出条目标 ``selected=True``。

    返回::

        {
          "entries": {url: {url, lastmod, lastmod_date, sources: [...]}},
          "duplicate_urls": [{url, chosen_lastmod, chosen_file, sources: [...]}],
          "raw_entry_total": 各文件条目数之和（含文件内/跨文件重复）,
          "unique_total": 去重后的 URL 数,
        }
    """
    entries: dict[str, dict] = {}
    raw_entry_total = 0
    for order, file in enumerate(files):
        if file.get("status") not in READABLE_STATUSES:
            continue
        file_entries = file.get("entries") or []
        raw_entry_total += len(file_entries)
        for position, entry in enumerate(file_entries):
            url = entry["url"]
            source = {
                "file": file["url"],
                "lastmod": entry.get("lastmod", ""),
                "lastmod_date": entry.get("lastmod_date", ""),
                "order": order,
                "position": position,
                "selected": False,
            }
            current = entries.get(url)
            if current is None:
                merged = {
                    "url": url,
                    "lastmod": entry.get("lastmod", ""),
                    "lastmod_date": entry.get("lastmod_date", ""),
                    "sources": [source],
                }
                source["selected"] = True
                entries[url] = merged
                continue
            current["sources"].append(source)
            incoming_key = _lastmod_sort_key(entry.get("lastmod", ""))
            current_key = _lastmod_sort_key(current["lastmod"])
            if incoming_key > current_key:
                current["lastmod"] = entry.get("lastmod", "")
                current["lastmod_date"] = entry.get("lastmod_date", "")
                for item in current["sources"]:
                    item["selected"] = False
                source["selected"] = True
            # 相等或更旧时保留原胜出方（更早的文件），出处照常累加

    duplicate_urls: list[dict] = []
    for url in sorted(entries):
        merged = entries[url]
        sources = merged["sources"]
        if len(sources) <= 1:
            continue
        chosen = next(item for item in sources if item["selected"])
        duplicate_urls.append(
            {
                "url": url,
                "chosen_lastmod": merged["lastmod"],
                "chosen_file": chosen["file"],
                "sources": [dict(item) for item in sources],
            }
        )

    return {
        "entries": entries,
        "duplicate_urls": duplicate_urls,
        "raw_entry_total": raw_entry_total,
        "unique_total": len(entries),
    }


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


def pending_diff(catalog_count: int, manifest: dict) -> dict:
    """读取不完整时的占位对账：不产出任何差异，绝不判收录完整。"""
    failed = [f for f in manifest["files"] if f["status"] not in READABLE_STATUSES]
    return {
        "only_in_catalog": [],
        "only_in_sitemap": [],
        "lastmod_mismatch": [],
        "counts": {
            "catalog": catalog_count,
            "sitemap": manifest["unique_total"],
            "only_in_catalog": 0,
            "only_in_sitemap": 0,
            "lastmod_mismatch": 0,
        },
        "verdict": "待核对",
        "conclusion": (
            "目录 %d 条；sitemap 共 %d 个文件，%d 个未成功读取（%s），本次只完成部分读取，"
            "不发布差异、不能判为收录完整；以下仅列出待核对范围。"
            % (
                catalog_count,
                manifest["file_total"],
                len(failed),
                "、".join(sorted({f["status"] for f in failed})),
            )
        ),
    }
