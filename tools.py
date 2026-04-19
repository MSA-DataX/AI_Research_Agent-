from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS


def _retry(fn: Callable, tries: int = 3, base_delay: float = 0.8) -> Any:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if attempt < tries - 1:
                time.sleep(base_delay * (1.8 ** attempt))
    if last:
        raise last

from config import RESULTS_DIR

_UA = "Mozilla/5.0 (compatible; AutonomousAgent/1.0)"

_SEARCH_CACHE: dict[tuple, list] = {}
_FETCH_CACHE: dict[tuple, str] = {}
_CACHE_MAX = 128


def _cache_put(cache: dict, key, value) -> None:
    if len(cache) >= _CACHE_MAX:
        cache.pop(next(iter(cache)))
    cache[key] = value


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s\-./]?)?(?:\(?\d{2,5}\)?[\s\-./]?){2,4}\d{2,6}")
_ADDR_RE = re.compile(
    r"\b\d{5}\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]{2,}(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]{2,})?\b"
)


def web_search(query: str, max_results: int = 8, region: str = "de-de") -> list[dict]:
    key = (query, max_results, region)
    if key in _SEARCH_CACHE:
        return _SEARCH_CACHE[key]
    results: list[dict] = []

    def _do() -> list[dict]:
        with DDGS() as ddgs:
            return list(
                ddgs.text(
                    query,
                    region=region,
                    safesearch="moderate",
                    max_results=max_results,
                )
            )

    try:
        raw = _retry(_do, tries=3)
    except Exception as e:
        return [{"title": "[search error]", "url": "", "snippet": str(e)}]
    for r in raw:
        results.append(
            {
                "title": r.get("title"),
                "url": r.get("href") or r.get("url"),
                "snippet": r.get("body"),
            }
        )
    _cache_put(_SEARCH_CACHE, key, results)
    return results


def fetch_url(url: str, max_chars: int = 8000) -> str:
    key = (url, max_chars)
    if key in _FETCH_CACHE:
        return _FETCH_CACHE[key]

    def _do() -> httpx.Response:
        resp = httpx.get(
            url,
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": _UA},
        )
        if resp.status_code >= 500:
            resp.raise_for_status()
        return resp

    try:
        r = _retry(_do, tries=3)
        r.raise_for_status()
    except Exception as e:
        return f"[fetch error] {e}"

    soup = BeautifulSoup(r.text, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    text = text[:max_chars]
    _cache_put(_FETCH_CACHE, key, text)
    return text


def extract_contacts(text: str) -> dict:
    t = text or ""
    emails = sorted({m.lower() for m in _EMAIL_RE.findall(t)})
    phones_raw = _PHONE_RE.findall(t)
    phones = sorted(
        {
            p.strip()
            for p in phones_raw
            if len(re.sub(r"\D", "", p)) >= 7 and len(re.sub(r"\D", "", p)) <= 15
        }
    )
    addresses = sorted({m.strip() for m in _ADDR_RE.findall(t)})
    return {"emails": emails, "phones": phones, "addresses": addresses}


def save_json(filename: str, data: Any) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename).strip("_")
    if not safe.endswith(".json"):
        safe += ".json"
    path = os.path.join(RESULTS_DIR, safe)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def web_search_parallel(queries: list[str], max_results: int = 5) -> dict:
    queries = [q for q in (queries or []) if q]
    if not queries:
        return {}
    with ThreadPoolExecutor(max_workers=min(len(queries), 5)) as ex:
        results = list(ex.map(lambda q: web_search(q, max_results=max_results), queries))
    return dict(zip(queries, results))


TOOLS = {
    "web_search": web_search,
    "web_search_parallel": web_search_parallel,
    "fetch_url": fetch_url,
    "extract_contacts": extract_contacts,
    "save_json": save_json,
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web (DuckDuckGo). Returns a list of {title, url, snippet}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query, in the target language if possible."},
                    "max_results": {"type": "integer", "default": 8, "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search_parallel",
            "description": "Run multiple web searches in parallel. Returns {query: [results]} map. Use when you need several related searches at once (much faster than sequential web_search calls).",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 5,
                    },
                    "max_results": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
                },
                "required": ["queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a URL and return cleaned page text (scripts/nav removed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 8000, "minimum": 500, "maximum": 40000},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_contacts",
            "description": "Extract emails, phone numbers and German postal addresses (5-digit PLZ + city) from a text blob. Use this on fetch_url output when the task asks for contact info.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_json",
            "description": "Save structured data as a JSON file in the results directory. Call this BEFORE finish.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "File name without extension is fine."},
                    "data": {"description": "Any JSON-serializable value (object, array, ...)."},
                },
                "required": ["filename", "data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "End the task. Return the final structured result. Only call after save_json.",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {"description": "Final structured output (object or array)."},
                },
                "required": ["result"],
            },
        },
    },
]
