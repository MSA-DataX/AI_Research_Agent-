from __future__ import annotations

from url_utils import canonicalize_url, same_url


def test_strips_www():
    assert canonicalize_url("https://www.example.com/foo") == "https://example.com/foo"


def test_lowercases_host():
    assert canonicalize_url("https://Example.COM/Foo") == "https://example.com/Foo"


def test_strips_trailing_slash():
    assert canonicalize_url("https://example.com/path/") == "https://example.com/path"


def test_preserves_root_slash():
    assert canonicalize_url("https://example.com/") == "https://example.com/"


def test_removes_utm_params():
    got = canonicalize_url("https://example.com/x?utm_source=google&utm_medium=cpc&id=42")
    assert "utm_source" not in got
    assert "utm_medium" not in got
    assert "id=42" in got


def test_removes_fbclid_gclid():
    got = canonicalize_url("https://example.com/x?fbclid=abc&gclid=xyz&q=real")
    assert "fbclid" not in got
    assert "gclid" not in got
    assert "q=real" in got


def test_drops_fragment():
    assert canonicalize_url("https://example.com/x#section") == "https://example.com/x"


def test_handles_empty():
    assert canonicalize_url("") == ""
    assert canonicalize_url(None) == ""


def test_returns_input_if_unparseable():
    result = canonicalize_url("not-a-url")
    assert result == "not-a-url"


def test_same_url_detects_equivalents():
    assert same_url(
        "https://www.example.com/path/?utm_source=x",
        "https://example.com/path",
    )
    assert same_url(
        "https://EXAMPLE.com/foo?a=1&b=2",
        "https://example.com/foo?b=2&a=1",
    )


def test_same_url_distinguishes_different():
    assert not same_url("https://a.com/x", "https://b.com/x")
    assert not same_url("https://example.com/foo", "https://example.com/bar")
