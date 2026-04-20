from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {
    "fbclid", "gclid", "msclkid", "yclid", "mc_cid", "mc_eid",
    "_ga", "_gl", "ref", "ref_src", "ref_url", "source",
    "igshid", "spm",
}


def canonicalize_url(url: str) -> str:
    """Return a normalized form of the URL suitable for dedup.

    Strips tracking params, lowercases host, removes leading 'www.',
    removes trailing slash, drops fragment.
    Returns the original input unchanged if parsing fails.
    """
    if not url or not isinstance(url, str):
        return url or ""
    try:
        p = urlparse(url.strip())
    except Exception:
        return url

    if not p.scheme or not p.netloc:
        return url

    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    kept = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=False)
        if not any(k.lower().startswith(pre) for pre in _TRACKING_PREFIXES)
        and k.lower() not in _TRACKING_KEYS
    ]
    kept.sort()
    query = urlencode(kept)

    return urlunparse((p.scheme.lower(), host, path, "", query, ""))


def same_url(a: str, b: str) -> bool:
    return canonicalize_url(a) == canonicalize_url(b)
