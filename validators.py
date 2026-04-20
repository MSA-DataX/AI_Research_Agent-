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


_TRUSTED_DOMAINS: dict[str, float] = {
    "wikipedia.org": 1.00,
    "wikidata.org": 1.00,
    "britannica.com": 0.90,
    "bundestag.de": 0.95,
    "bundesregierung.de": 0.95,
    "destatis.de": 0.95,
    "europa.eu": 0.90,
    "bmi.bund.de": 0.90,
    "statistik-berlin-brandenburg.de": 0.85,
    "tagesschau.de": 0.85,
    "handelsblatt.com": 0.80,
    "faz.net": 0.80,
    "sueddeutsche.de": 0.80,
    "zeit.de": 0.80,
    "spiegel.de": 0.80,
    "welt.de": 0.75,
    "reuters.com": 0.85,
    "bbc.com": 0.85,
    "nytimes.com": 0.85,
    "statista.com": 0.75,
    "handelsregister.de": 0.85,
    "ihk.de": 0.80,
}

_LOW_TRUST_HINTS = ("quiz", "listicle", "top10", "rank-", "freebie", "spam")


def _domain_trust_score(url: str) -> tuple[str, float]:
    if not url:
        return "", 0.0
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return "", 0.0
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return "", 0.0

    if host in _TRUSTED_DOMAINS:
        return host, _TRUSTED_DOMAINS[host]
    for d, score in _TRUSTED_DOMAINS.items():
        if host == d or host.endswith("." + d):
            return host, score

    if host.endswith(".gov") or ".gov." in host or host.endswith(".europa.eu"):
        return host, 0.90
    if host.endswith(".edu") or ".ac." in host:
        return host, 0.80

    score = 0.35
    if any(p in host for p in _LOW_TRUST_HINTS):
        score = 0.15
    return host, score


def method_domain_trust(row: dict, *_ignored, **__ignored) -> dict:
    """Bewertet die Vertrauenswürdigkeit der Quell-Domain (Wikipedia > Staat > Medien > Unknown > Quiz)."""
    src = row.get("source_url") or row.get("source") or row.get("url") or ""
    if not src:
        return {"supported": False, "reason": "no source_url", "score": 0.0}
    host, score = _domain_trust_score(src)
    if score >= 0.80:
        label = "high"
    elif score >= 0.55:
        label = "medium"
    elif score >= 0.30:
        label = "low"
    else:
        label = "distrust"
    return {
        "supported": score >= 0.55,
        "reason": f"domain '{host}' trust={score:.2f} ({label})",
        "score": round(score, 2),
        "domain": host,
    }


def method_field_completeness(row: dict, *_ignored, **__ignored) -> dict:
    """Prueft wie viele der erwarteten Felder ueberhaupt ausgefuellt sind (keine null/leer)."""
    ignored = _SKIP_KEYS
    values = [(k, v) for k, v in row.items() if k not in ignored]
    if not values:
        return {"supported": False, "reason": "no verifiable fields"}
    filled = sum(1 for _, v in values if v not in (None, "", [], {}))
    total = len(values)
    ratio = filled / total
    return {
        "supported": ratio >= 0.80,
        "reason": f"{filled}/{total} fields filled ({int(ratio*100)}%)",
        "filled": filled,
        "total": total,
    }


def method_consistency(row: dict, chat_fn=None, *_ignored, **__ignored) -> dict:
    """Prueft ob die Felder intern konsistent sind (Weltwissen).
    Fraegt LLM: widersprechen sich Felder untereinander? (z.B. Berlin in Frankreich?)
    Braucht keine Seite - nutzt nur Modell-Wissen."""
    if chat_fn is None:
        return {"supported": False, "reason": "chat function not provided"}
    if not isinstance(row, dict) or not row:
        return {"supported": False, "reason": "empty item"}

    item = json.dumps(row, ensure_ascii=False)
    prompt = (
        "Task: Are the fields of this item INTERNALLY CONSISTENT based on well-known facts?\n"
        "Detect factual contradictions (e.g. 'Berlin' with country='France', or Berlin with bundesland='Brandenburg' - Berlin IS its own Bundesland).\n"
        "Ignore source URLs - only evaluate the field values.\n\n"
        f"ITEM: {item}\n\n"
        'Respond with ONE JSON object only (no prose, no code fences):\n'
        '{"consistent": true|false, "confidence": 0-100, "reason": "short explanation"}'
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
        "supported": bool(data.get("consistent")),
        "confidence": int(data.get("confidence", 50)),
        "reason": str(data.get("reason", ""))[:250],
    }


def method_relationship_validation(row: dict, chat_fn=None, *_ignored, **__ignored) -> dict:
    """Prueft bekannte Beziehungen zwischen Feldern:
    - city -> country / federal state
    - company -> sector / headquarters
    - person -> organization
    Fraegt LLM ob die Relationen in Welt-Wissen korrekt sind."""
    if chat_fn is None:
        return {"supported": False, "reason": "chat function not provided"}
    if not isinstance(row, dict) or len(row) < 2:
        return {"supported": False, "reason": "need at least 2 fields to check relationships"}

    item = json.dumps(row, ensure_ascii=False)
    prompt = (
        "Task: Verify the RELATIONSHIPS implied between the fields of this item.\n"
        "Common relations to check:\n"
        "  - city / region / state / country (geographic containment)\n"
        "  - company / industry / headquarters / parent\n"
        "  - person / organization / role\n"
        "  - product / brand / manufacturer\n"
        "Ignore URL fields. If the item contains no verifiable relation, answer supported=true.\n\n"
        f"ITEM: {item}\n\n"
        'Respond with ONE JSON object only:\n'
        '{"supported": true|false, "relations_checked": ["city->state", ...], "confidence": 0-100, "reason": "short"}'
    )
    try:
        raw = chat_fn(
            [
                {"role": "system", "content": "You are a strict relationship validator. Output valid JSON only."},
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

    rels = data.get("relations_checked") or []
    return {
        "supported": bool(data.get("supported")),
        "confidence": int(data.get("confidence", 50)),
        "relations_checked": rels if isinstance(rels, list) else [],
        "reason": str(data.get("reason", ""))[:250],
    }


METHODS: dict[str, str] = {
    "name_substring": "Name als Substring im Seitentext",
    "all_fields": "Alle String-Felder müssen im Seitentext stehen",
    "cross_source": "Web-Suche: wie viele verschiedene Domains bestätigen den Namen",
    "llm_semantic": "LLM prüft semantisch ob Seite das Item inhaltlich stützt",
    "domain_trust": "Autorität der Quell-Domain (Wikipedia/Staat/Medien > Quiz/Listicle)",
    "field_completeness": "Anteil der Felder die tatsächlich ausgefüllt sind (nicht null/leer)",
    "consistency": "LLM prüft ob Item-Felder intern konsistent sind (z.B. Berlin ≠ Brandenburg)",
    "relationship_validation": "LLM prüft bekannte Beziehungen (city→state, company→industry, etc.)",
}

METHOD_WEIGHTS: dict[str, float] = {
    "name_substring": 0.05,
    "all_fields": 0.10,
    "cross_source": 0.14,
    "llm_semantic": 0.22,
    "domain_trust": 0.12,
    "field_completeness": 0.05,
    "consistency": 0.18,
    "relationship_validation": 0.14,
}


def verdict_for_row(method_results: dict, weights: dict | None = None) -> tuple[str, int]:
    """Gewichtete Confidence. Methoden ohne Weight bekommen 1/n als Fallback."""
    if not method_results:
        return "unverified", 0
    w = weights or METHOD_WEIGHTS
    active = {m: r for m, r in method_results.items()}
    total_w = sum(w.get(m, 0) for m in active)
    if total_w <= 0:
        total_w = len(active)
        got = sum(1 for r in active.values() if r.get("supported"))
    else:
        got = sum(w.get(m, 0) for m, r in active.items() if r.get("supported"))
    score = got / total_w
    conf = int(round(score * 100))
    if conf >= 85:
        label = "high"
    elif conf >= 60:
        label = "medium"
    elif conf >= 30:
        label = "low"
    else:
        label = "unverified"
    return label, conf
