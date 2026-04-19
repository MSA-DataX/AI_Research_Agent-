from __future__ import annotations

import json
import re
import unicodedata
from urllib.parse import urlparse


_SKIP_KEYS = {"source_url", "source", "url", "sourceUrl", "href"}


def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _row_name(row: dict) -> str:
    for k in ("name", "title", "company", "firma", "label"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for v in row.values():
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def method_name_substring(row: dict, page_text: str) -> dict:
    """Prüft ob der Haupt-Name als Substring (case-insensitive) im Seitentext steht."""
    name = _row_name(row)
    if not name:
        return {"supported": False, "reason": "no name field"}
    if not page_text or page_text.startswith("[fetch error]"):
        return {"supported": False, "reason": "no page content"}
    ok = name.lower() in page_text.lower()
    return {"supported": ok, "reason": "name found" if ok else "name not in page"}


def method_all_fields(row: dict, page_text: str) -> dict:
    """Prüft ob ALLE String-Felder (außer URL) im Seitentext vorkommen (normalisiert)."""
    if not page_text or page_text.startswith("[fetch error]"):
        return {"supported": False, "reason": "no page content"}
    hay = _norm(page_text)
    checked: list[str] = []
    missing: list[str] = []
    for k, v in row.items():
        if k in _SKIP_KEYS:
            continue
        if not isinstance(v, str) or len(v.strip()) < 2:
            continue
        checked.append(k)
        needle = _norm(v)
        if needle and needle not in hay:
            missing.append(f"{k}='{v[:40]}'")
    if not checked:
        return {"supported": False, "reason": "no verifiable string fields"}
    ok = len(missing) == 0
    return {
        "supported": ok,
        "reason": "all fields found" if ok else f"missing: {', '.join(missing[:3])}",
        "fields_checked": len(checked),
        "fields_missing": len(missing),
    }


def method_cross_source(row: dict, search_fn, max_results: int = 5) -> dict:
    """
    Sucht im Web nach row's Haupt-Feldern. Zählt wie viele verschiedene Domains
    den Namen in Titel/Snippet bestätigen.
    """
    name = _row_name(row)
    if not name:
        return {"supported": False, "reason": "no name"}

    query_parts = [name]
    for k in ("date", "datum", "year", "jahr", "city", "stadt", "country"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            query_parts.append(v.strip())
            break
    query = " ".join(query_parts)[:100]

    try:
        results = search_fn(query, max_results=max_results)
    except Exception as e:
        return {"supported": False, "reason": f"search error: {e}"}

    if not isinstance(results, list):
        return {"supported": False, "reason": "no search results"}

    needle = name.lower()
    confirming_urls: list[str] = []
    domains: set[str] = set()
    for r in results:
        if not isinstance(r, dict):
            continue
        blob = ((r.get("title") or "") + " " + (r.get("snippet") or "")).lower()
        if needle in blob:
            url = r.get("url") or ""
            if url:
                confirming_urls.append(url)
                host = urlparse(url).netloc.lower().lstrip("www.")
                if host:
                    domains.add(host)

    n_domains = len(domains)
    if n_domains >= 3:
        label = "high"
    elif n_domains == 2:
        label = "medium"
    elif n_domains == 1:
        label = "low"
    else:
        label = "unverified"

    return {
        "supported": n_domains >= 2,
        "reason": f"{n_domains} distinct domain(s) confirm in title/snippet",
        "n_domains": n_domains,
        "label": label,
        "confirming_urls": confirming_urls[:5],
    }


_JSON_RE = re.compile(r"\{.*\}", re.S)


def method_llm_semantic(row: dict, page_text: str, chat_fn) -> dict:
    """LLM-basierter Fact-Checker: prüft semantisch ob die Seite das Item stützt."""
    if not page_text or page_text.startswith("[fetch error]"):
        return {"supported": False, "reason": "no page content"}
    if chat_fn is None:
        return {"supported": False, "reason": "chat function not provided"}

    excerpt = page_text[:3500]
    claim = json.dumps(row, ensure_ascii=False)

    prompt = (
        "Does the following web page excerpt support ALL key claims in the item?\n"
        "Be strict about specific values (dates, years, numbers, addresses).\n\n"
        f"ITEM: {claim}\n\n"
        f"PAGE EXCERPT:\n{excerpt}\n\n"
        "Respond with ONE JSON object only (no prose, no code fences):\n"
        '{"supported": true|false, "confidence": 0-100, "reason": "short why"}'
    )
    try:
        raw = chat_fn(
            [
                {"role": "system", "content": "You are a strict fact-checker. Output valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
        )
    except Exception as e:
        return {"supported": False, "reason": f"llm error: {str(e)[:120]}"}

    if not raw:
        return {"supported": False, "reason": "empty llm response"}

    m = _JSON_RE.search(raw.strip().strip("`"))
    if not m:
        return {"supported": False, "reason": "could not parse llm JSON"}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {"supported": False, "reason": "invalid llm JSON"}

    return {
        "supported": bool(data.get("supported")),
        "confidence": int(data.get("confidence", 50)),
        "reason": str(data.get("reason", ""))[:250],
    }


METHODS: dict[str, str] = {
    "name_substring": "Name als Substring im Seitentext",
    "all_fields": "Alle String-Felder müssen im Seitentext stehen",
    "cross_source": "Web-Suche: wie viele verschiedene Domains bestätigen den Namen",
    "llm_semantic": "LLM prüft semantisch ob Seite das Item inhaltlich stützt",
}


def verdict_for_row(method_results: dict) -> tuple[str, int]:
    """Aggregiert Methoden-Ergebnisse zu einem Verdict + Confidence 0-100."""
    n = len(method_results)
    if n == 0:
        return "unverified", 0
    supported_count = sum(1 for r in method_results.values() if r.get("supported"))
    ratio = supported_count / n
    conf = int(round(ratio * 100))
    if conf >= 100:
        label = "high"
    elif conf >= 60:
        label = "medium"
    elif conf > 0:
        label = "low"
    else:
        label = "unverified"
    return label, conf
