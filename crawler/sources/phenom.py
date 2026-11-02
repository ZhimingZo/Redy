"""Phenom career sites that embed the search result in the page HTML.

Lilly, UPS, United, and Southwest do this. The public page is
`{origin}/{locale}/search-results?from={offset}` and the next page is the
`hits` count, not a guess. Job text lives on the job URL built from the id.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime

import requests

from ..classify import classify_family, level_from_title, us_locations
from .ats import pay_from_text, strip_html, yoe_snippets

log = logging.getLogger("crawler")

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html",
}
_TIMEOUT = 30


def search_payload(html: str) -> dict | None:
    """The eagerLoadRefineSearch object Phenom prints into the search page."""
    key = '"eagerLoadRefineSearch"'
    i = (html or "").find(key)
    if i < 0:
        return None
    start = html.find("{", html.find(":", i))
    if start < 0:
        return None
    depth = 0
    for j in range(start, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start : j + 1])
                except json.JSONDecodeError:
                    return None
    return None


def phenom_places(job: dict) -> list[str]:
    places = []
    for item in job.get("multi_location_array") or []:
        if isinstance(item, dict) and item.get("location"):
            places.append(item["location"])
        elif isinstance(item, str):
            places.append(item)
    if places:
        return places
    city = (job.get("cityState") or job.get("city") or "").strip()
    country = (job.get("country") or "").strip()
    if city and country and country.lower() not in city.lower():
        return [f"{city}, {country}"]
    if city:
        return [city]
    return [country] if country else []


def posted_at(text: str | None) -> str | None:
    raw = (text or "")[:19]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", raw):
        return raw + "+00:00"
    return None


def job_url(search_url: str, job: dict) -> str:
    """Search URL is `{origin}/{locale}/search-results`. The job page sits beside it."""
    base = re.sub(r"/search-results.*$", "", search_url).rstrip("/")
    ident = str(job.get("jobId") or job.get("reqId") or "")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", job.get("title") or "job").strip("-") or "job"
    return f"{base}/job/{ident}/{slug}"


def _description(html: str) -> str:
    """Longest description string on a job page. The teaser is shorter and comes first."""
    best = ""
    for match in re.finditer(r'"(?:description|jobDescription)"\s*:\s*"((?:\\.|[^"\\])*)"', html or ""):
        text = json.loads('"' + match.group(1) + '"')
        if len(text) > len(best):
            best = text
    return best


def keep(title: str, places: list[str]) -> bool:
    if not classify_family(title) or level_from_title(title) == "junior":
        return False
    return bool(us_locations(places))


def fetch(search_url: str, base: dict, get=None) -> list[dict]:
    """Every US eng/PM role on one Phenom search. `get` is injectable for tests."""
    getter = get or (lambda url: requests.get(url, headers=_UA, timeout=_TIMEOUT).text)
    seen: set[str] = set()
    picked: list[dict] = []
    offset = 0
    total = None
    step = 10
    while total is None or offset < total:
        url = search_url + (("&" if "?" in search_url else "?") + f"from={offset}&s=1")
        try:
            payload = search_payload(getter(url)) or {}
        except requests.RequestException as e:
            log.warning("phenom %s offset %s failed: %s", search_url, offset, str(e)[:100])
            break
        jobs = ((payload.get("data") or {}).get("jobs")) or []
        if total is None:
            total = int(payload.get("totalHits") or 0)
            step = int(payload.get("hits") or len(jobs) or 10)
        if not jobs:
            break
        for job in jobs:
            ident = str(job.get("jobId") or job.get("reqId") or "")
            title = re.sub(r"\s+", " ", job.get("title") or "").strip()
            if not ident or ident in seen or re.match(r"do not apply", title, re.I):
                continue
            seen.add(ident)
            places = phenom_places(job)
            if not keep(title, places):
                continue
            picked.append({
                "id": ident,
                "t": title,
                "loc": places,
                "at": posted_at(job.get("postedDate")),
                "url": job_url(search_url, job),
                "teaser": job.get("descriptionTeaser") or "",
            })
        offset += step
        if offset > 20000:
            break
        time.sleep(0.15)
    log.info("phenom %s: %s listed, %s kept", search_url, total, len(picked))

    out = []
    for row in picked:
        desc = ""
        if get is None:
            try:
                desc = _description(getter(row["url"]))
            except requests.RequestException:
                desc = ""
            time.sleep(0.1)
        text = strip_html(desc or row["teaser"])
        out.append({
            **base,
            "p": "phenom",
            "id": row["id"],
            "t": row["t"],
            "loc": row["loc"],
            "ctry": "",
            "rem": any(re.search(r"remote", p, re.I) for p in row["loc"]),
            "at": row["at"],
            "url": row["url"],
            "pay": pay_from_text(text),
            "yoe": yoe_snippets(text),
            "desc": text,
        })
    return out
