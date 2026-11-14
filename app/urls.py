"""URL 规范化：纯函数，无 IO，供采集器、对账与页面共用。

规则（与 README「核心规则 1」逐条对应）：
1. 去掉 fragment（#...）
2. 去掉追踪参数 utm_* / spm / from / scm / share_token
3. query 键排序
4. 相对链接转绝对（按当前页 URL 解析）
5. host 小写
6. 去掉默认端口（http:80 / https:443）
7. 合并重复斜杠
8. 去掉尾部斜杠（根路径除外）
9. 保留已有百分号编码，不做二次编码（全程不 unquote/quote，中文按原字节保留）
10. 路径大小写敏感（不做任何大小写折叠）
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")

TRACKING_EXACT = {"spm", "from", "scm", "share_token"}
TRACKING_PREFIXES = ("utm_",)
DEFAULT_PORTS = {"http": 80, "https": 443}


def is_tracking_key(key: str) -> bool:
    low = key.lower()
    return low in TRACKING_EXACT or any(low.startswith(p) for p in TRACKING_PREFIXES)


def _normalize_path(raw_path: str, changes: list[str]) -> str:
    path = raw_path or "/"
    if not path.startswith("/"):
        path = "/" + path
    if "//" in path:
        changes.append("合并重复斜杠")
        while "//" in path:
            path = path.replace("//", "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
        changes.append("去尾部斜杠")
    return path or "/"


def _normalize_query(raw_query: str, changes: list[str]) -> str:
    if not raw_query:
        return ""
    pairs = [p for p in raw_query.split("&") if p]
    kept: list[str] = []
    dropped: list[str] = []
    for pair in pairs:
        key = pair.split("=", 1)[0]
        if is_tracking_key(key):
            dropped.append(key)
        else:
            kept.append(pair)
    if dropped:
        changes.append("去追踪参数(" + ",".join(sorted(set(dropped))) + ")")
    if kept:
        before = "&".join(kept)
        kept = sorted(kept, key=lambda p: (p.split("=", 1)[0], p))
        if "&".join(kept) != before:
            changes.append("query 键排序")
    return "&".join(kept)


def normalize_with_evidence(raw: str, base: str | None = None) -> tuple[str, list[str]]:
    """返回 (规范化 URL, 变更依据)。无法规范化时抛 ValueError。"""
    changes: list[str] = []
    text = (raw or "").strip()
    if not text:
        raise ValueError("空 URL")

    if base:
        joined = urljoin(base, text)
        if joined != text:
            changes.append("相对转绝对")
        text = joined

    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise ValueError("仅支持 http/https：%s" % (raw or ""))
    if scheme != parts.scheme:
        changes.append("scheme 小写")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("缺少 host：%s" % (raw or ""))
    if parts.hostname and host != parts.hostname:
        changes.append("host 小写")

    port: int | None
    try:
        port = parts.port
    except ValueError as exc:  # 非法端口
        raise ValueError("非法端口：%s" % (raw or "")) from exc
    if port is not None and DEFAULT_PORTS[scheme] == port:
        changes.append("去默认端口")
        netloc = host
    elif port is not None:
        netloc = "%s:%d" % (host, port)
    else:
        netloc = host

    path = _normalize_path(parts.path, changes)
    query = _normalize_query(parts.query, changes)
    if parts.fragment:
        changes.append("去 fragment")

    normalized = urlunsplit((scheme, netloc, path, query, ""))
    return normalized, changes


def normalize_url(raw: str, base: str | None = None) -> str:
    return normalize_with_evidence(raw, base)[0]


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("缺少 host：%s" % url)
    port = parts.port
    netloc = host if port in (None, DEFAULT_PORTS.get(parts.scheme.lower())) else "%s:%d" % (host, port)
    return "%s://%s" % (parts.scheme.lower(), netloc)


def is_same_site(url: str, target_host: str) -> bool:
    return host_of(url) == (target_host or "").lower()


def require_target(raw: str) -> str:
    """校验页面提交的目标站地址，返回规范化后的入口 URL（根路径保留 '/'）。"""
    text = (raw or "").strip()
    if not text:
        raise ValueError("请填写目标站地址，例如 http://sample-site")
    if any(ch in text for ch in " \t\r\n"):
        raise ValueError("目标站地址不能包含空白字符：%s" % raw)
    if "://" not in text:
        text = "http://" + text
    normalized, _ = normalize_with_evidence(text)
    parts = urlsplit(normalized)
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("目标站地址缺少主机名：%s" % raw)
    if not HOST_RE.match(host):
        raise ValueError("目标站主机名不合法：%s" % host)
    return normalized
