"""URL 规范化表驱动测试：≥8 个场景，覆盖 README 第 1 条规则的全部子项。"""

from __future__ import annotations

import pytest

from app.urls import host_of, normalize_url, normalize_with_evidence, require_target

CASES = [
    # (说明, 原始链接, base, 期望结果)
    ("去 fragment", "http://sample-site/list/detail-12.html#top", None, "http://sample-site/list/detail-12.html"),
    (
        "去 utm_*/spm/from 并排序 query",
        "http://sample-site/list/x.html?utm_source=wechat&from=list&spm=a1&b=2&a=1",
        None,
        "http://sample-site/list/x.html?a=1&b=2",
    ),
    (
        "相对转绝对",
        "./detail-12.html",
        "http://sample-site/list/page-2.html",
        "http://sample-site/list/detail-12.html",
    ),
    ("host 小写 + 去默认端口 80", "HTTP://Sample-Site:80/list/", None, "http://sample-site/list"),
    ("https 去默认端口 443", "https://a.gov.cn:443/x", None, "https://a.gov.cn/x"),
    ("非默认端口保留", "http://a.gov.cn:8080/x/", None, "http://a.gov.cn:8080/x"),
    ("合并重复斜杠", "http://a.gov.cn//a///b/", None, "http://a.gov.cn/a/b"),
    ("根路径保留尾斜杠", "http://a.gov.cn/", None, "http://a.gov.cn/"),
    (
        "保留已有百分号编码，不二次编码",
        "http://a.gov.cn/%E6%94%BF%E5%8A%A1/%e4%b8%ad",
        None,
        "http://a.gov.cn/%E6%94%BF%E5%8A%A1/%e4%b8%ad",
    ),
    ("已双重编码的百分号不解码", "http://a.gov.cn/x%2520y", None, "http://a.gov.cn/x%2520y"),
    ("路径大小写敏感（不折叠）", "http://a.gov.cn/List/Page-2.html", None, "http://a.gov.cn/List/Page-2.html"),
    ("query 值里的中文编码保留", "http://a.gov.cn/s?b=%E4%B8%AD&a=1", None, "http://a.gov.cn/s?a=1&b=%E4%B8%AD"),
    ("无 query 时不留问号", "http://a.gov.cn/s?", None, "http://a.gov.cn/s"),
]


@pytest.mark.parametrize("title,raw,base,expected", CASES, ids=[case[0] for case in CASES])
def test_normalize_table(title, raw, base, expected):
    assert normalize_url(raw, base) == expected, title


def test_changes_evidence_tracks_tracking_params():
    url, changes = normalize_with_evidence("http://sample-site/list/x.html?utm_source=wechat&from=list#top")
    assert url == "http://sample-site/list/x.html"
    assert "去 fragment" in changes
    assert "相对转绝对" not in changes
    assert any(item.startswith("去追踪参数") for item in changes)


def test_changes_evidence_tracks_relative_and_trailing_slash():
    url, changes = normalize_with_evidence("./detail-12.html/", "http://sample-site/list/page-2.html")
    assert url == "http://sample-site/list/detail-12.html"
    assert "相对转绝对" in changes
    assert "去尾部斜杠" in changes


def test_root_target_keeps_slash_and_none_scheme_defaults_to_http():
    assert require_target("sample-site") == "http://sample-site/"
    assert require_target("http://sample-site") == "http://sample-site/"


def test_target_rejects_whitespace_and_bad_scheme():
    with pytest.raises(ValueError):
        require_target("not a url")
    with pytest.raises(ValueError):
        require_target("ftp://sample-site")
    with pytest.raises(ValueError):
        require_target("   ")


def test_host_of_is_lowercase():
    assert host_of("http://Sample-Site/list/page-2.html") == "sample-site"
