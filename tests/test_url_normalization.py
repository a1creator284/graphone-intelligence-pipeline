from __future__ import annotations

from src.extraction.urls import normalize_url


def test_trailing_slash_normalized():
    assert normalize_url("https://example.com/a/") == normalize_url("https://example.com/a")


def test_root_trailing_slash_preserved():
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_tracking_params_stripped():
    a = normalize_url("https://example.com/a?utm_source=twitter&utm_campaign=x")
    b = normalize_url("https://example.com/a")
    assert a == b


def test_fragment_stripped():
    assert normalize_url("https://example.com/a#section2") == normalize_url("https://example.com/a")


def test_scheme_and_host_lowercased():
    assert normalize_url("HTTPS://Example.COM/a") == "https://example.com/a"


def test_non_tracking_query_params_preserved():
    a = normalize_url("https://example.com/a?id=123")
    assert "id=123" in a


def test_query_param_order_normalized():
    a = normalize_url("https://example.com/a?b=2&a=1")
    b = normalize_url("https://example.com/a?a=1&b=2")
    assert a == b


def test_distinct_urls_remain_distinct():
    assert normalize_url("https://example.com/a") != normalize_url("https://example.com/b")
