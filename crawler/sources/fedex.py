"""FedEx lists jobs on its own Paradox career site.

The search page prints `window.__PRELOAD_STATE__` with one page of jobs.
`/jobs/page/{n}?keyword=` is the next page. The description is JSON-LD on
the job page, which is `{origin}/{originalURL}`.
"""
from __future__ import annotations

import json
import logging
import re
import time

import requests

from ..classify import classify_family, level_from_title, us_locations
from .ats import pay_from_text, strip_html, yoe_snippets

log = logging.getLogger("crawler")

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html",
}
_TIMEOUT = 30
_SEARCHES = (
    "https://careers.fedex.com/jobs?keyword=software+engineer",
    "https://careers.fedex.com/jobs?keyword=developer",
    "https://careers.fedex.com/jobs?keyword=product+manager",
    "https://careers.fedex.com/jobs?keyword=data+engineer",
)
_MAX_PAGES = 40


def preload(html: str) -> dict | None:
    match = re.search(r"window\.__PRELOAD_STATE__\s*=\s*(\{.*?\})\s*;\s*window\.__BUILD__", html or "", re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def page_url(search_url: str, page: int) -> str:
    base, _, query = search_url.partition("?")
    if page > 1:
        base = base.rstrip("/") + f"/page/{page}"
    return base + ("?" + query if query else "")


def fedex_places(job: dict) -> list[str]:
    places = []
    for loc in job.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        city = (loc.get("city") or "").strip()
        state = (loc.get("stateAbbr") or loc.get("state") or "").strip()
        country = (loc.get("country") or "").strip()
        bit = ", ".join(p for p in (city, state) if p)
        if country.lower() in ("united states", "usa", "us"):
            places.append(f"{bit}, United States" if bit else "United States")
        else:
            place = ", ".join(p for p in (bit, country) if p)
            if place:
                places.append(place)
    if job.get("isRemote"):
        places = [p if re.search(r"remote", p, re.I) else f"Remote, {p}" for p in places] or ["Remote"]
    return places


def job_url(search_url: str, job: dict) -> str:
    origin = search_url.split("/jobs")[0].rstrip("/")
    path = (job.get("originalURL") or "").lstrip("/")
    return f"{origin}/{path}" if path else ""


def posting(html: str) -> dict:
    """JobPosting JSON-LD. Description is HTML; datePosted is a calendar day when present."""
    match = re.search(r'<script type="application/ld\+json">(\{.*?\})</script>', html or "", re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    if isinstance(data, list):
        data = next((item for item in data if isinstance(item, dict) and item.get("@type") == "JobPosting"), {})
    return data if isinstance(data, dict) else {}


def posted_at(value: str | None) -> str | None:
    raw = (value or "")[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return f"{raw}T12:00:00+00:00"
    return None


def keep(title: str, places: list[str]) -> bool:
    if not classify_family(title) or level_from_title(title) == "junior":
        return False
    return bool(us_locations(places))


def fetch(base: dict, get=None, searches: tuple[str, ...] | None = None, platform: str = "fedex") -> list[dict]:
    getter = get or (lambda url: requests.get(url, headers=_UA, timeout=_TIMEOUT).text)
    seen: set[str] = set()
    picked: list[dict] = []
    for search_url in searches or _SEARCHES:
        page = 1
        seen_count = 0
        total = None
        while page <= _MAX_PAGES:
            try:
                state = preload(getter(page_url(search_url, page))) or {}
            except requests.RequestException as e:
                log.warning("%s %s page %s failed: %s", platform, search_url, page, str(e)[:100])
                break
            search = state.get("jobSearch") or {}
            jobs = search.get("jobs") or []
            if total is None:
                total = int(search.get("totalJob") or 0)
            if not jobs:
                break
            for job in jobs:
                ident = str(job.get("reference") or "")
                title = re.sub(r"\s+", " ", job.get("title") or "").strip()
                if not ident or ident in seen:
                    continue
                seen.add(ident)
                places = fedex_places(job)
                if not keep(title, places):
                    continue
                picked.append({
                    "id": ident,
                    "t": title,
                    "loc": places,
                    "url": job_url(search_url, job),
                    "rem": bool(job.get("isRemote")),
                })
            seen_count += len(jobs)
            if total and seen_count >= total:
                break
            page += 1
            time.sleep(0.15)
        log.info("%s %s: %s listed", platform, search_url, total)

    out = []
    for row in picked:
        desc = ""
        when = None
        if get is None and row["url"]:
            try:
                card = posting(getter(row["url"]))
                desc = card.get("description") or ""
                when = posted_at(card.get("datePosted"))
            except requests.RequestException:
                desc = ""
            time.sleep(0.1)
        text = strip_html(desc)
        out.append({
            **base,
            "p": platform,
            "id": row["id"],
            "t": row["t"],
            "loc": row["loc"],
            "ctry": "",
            "rem": row["rem"] or any(re.search(r"remote", p, re.I) for p in row["loc"]),
            "at": when,
            "url": row["url"],
            "pay": pay_from_text(text),
            "yoe": yoe_snippets(text),
            "desc": text,
        })
    log.info("%s kept %s", platform, len(out))
    return out
