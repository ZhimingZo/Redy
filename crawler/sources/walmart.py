"""Walmart's own careers site. The list is a persisted GraphQL search.

Store jobs are a different area. Technology is the area that carries the
engineering roles. The job page prints the description into __NEXT_DATA__.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

import requests

from ..classify import classify_family, level_from_title, us_locations
from .ats import pay_from_text, strip_html, yoe_snippets

log = logging.getLogger("crawler")

_QUERY = "b0467c1f-f578-4261-9280-0ea4614f251c"
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://careers.walmart.com",
    "Referer": "https://careers.walmart.com/us/en/results",
}
_HTML = {
    "User-Agent": _UA["User-Agent"],
    "Accept": "text/html",
}


def walmart_place(job: dict) -> list[str]:
    city = (job.get("city") or "").strip().title()
    state = (job.get("state") or "").strip()
    country = (job.get("country") or "").strip()
    if city and state:
        place = f"{city}, {state}"
    else:
        place = city or state
    if country in {"US", "USA"} and place:
        place = f"{place}, United States"
    return [place] if place else []


def posted_at(millis) -> str | None:
    if not isinstance(millis, (int, float)) or millis <= 0:
        return None
    day = datetime.fromtimestamp(millis / 1000, timezone.utc).date()
    return f"{day.isoformat()}T12:00:00+00:00"


def _search(page: int, query: str, filters: str) -> dict:
    body = {
        "queryId": _QUERY,
        "variables": {
            "chatRequest": {
                "messages": [{"role": "user", "content": [{"type": "text", "text": query or "jobs"}]}],
                "thread_id": str(uuid.uuid4()),
                "channel": "job_search",
                "context": {
                    "job_search_context": {
                        "refined_query": query,
                        "direct_search": True,
                        "locale": "en_US",
                        "sort": "relevance",
                        "active_tab": "jobs",
                        "management_levels": [],
                        "content_page": 0,
                        "future_roles_page": 0,
                        "job_page": page,
                        "filters": filters,
                    }
                },
            }
        },
        "headers": {},
    }
    response = requests.post("https://careers.walmart.com/api/graphql", json=body, headers=_UA, timeout=30)
    response.raise_for_status()
    messages = (((response.json().get("data") or {}).get("jobSearchAssistant") or {}).get("tool_messages")) or []
    return (messages[0] or {}).get("artifact") or {}


def _description(html: str) -> str:
    marker = 'id="__NEXT_DATA__"'
    i = html.find(marker)
    if i < 0:
        return ""
    start = html.find(">", i)
    end = html.find("</script>", start)
    try:
        data = json.loads(html[start + 1 : end])
    except json.JSONDecodeError:
        return ""
    details = ((data.get("props") or {}).get("pageProps") or {}).get("jobDetails") or {}
    return details.get("jobPostingDescription") or details.get("description") or ""


def fetch(base: dict) -> list[dict]:
    """Technology roles, then a product search so PM titles are not missed."""
    seen: set[str] = set()
    jobs: list[dict] = []
    # An empty filter matches every store job. Technology and Corporate are the boards that carry eng and PM roles.
    searches = (
        ("engineer", "areas IN ['Technology']"),
        ("product manager", "areas IN ['Technology']"),
        ("product manager", "areas IN ['Corporate']"),
    )
    for query, filters in searches:
        page = 0
        total = None
        misses = 0
        while total is None or page * 10 < total:
            try:
                art = _search(page, query, filters)
            except (requests.RequestException, ValueError, IndexError, KeyError) as e:
                log.warning("walmart %s page %s failed: %s", query, page, str(e)[:120])
                misses += 1
                page += 1
                if misses >= 3 or page > 400:
                    break
                continue
            misses = 0
            batch = art.get("jobs") or []
            if total is None:
                total = int(art.get("total_jobs") or 0)
                log.info("walmart %s: %s jobs", query, total)
            if not batch:
                break
            for job in batch:
                ident = str(job.get("job_id") or "")
                if ident and ident not in seen:
                    seen.add(ident)
                    jobs.append(job)
            page += 1
            if page > 400:
                break
            time.sleep(0.15)

    picked = []
    for job in jobs:
        title = re.sub(r"\s+", " ", job.get("jobPostingTitle") or job.get("title") or "").strip()
        places = walmart_place(job)
        ident = str(job.get("job_id") or "")
        if not ident or not classify_family(title) or level_from_title(title) == "junior":
            continue
        if not us_locations(places):
            continue
        picked.append((job, title, places, ident))
    log.info("walmart kept %s before descriptions", len(picked))

    out = []
    for job, title, places, ident in picked:
        desc = ""
        try:
            page = requests.get(f"https://careers.walmart.com/us/en/jobs/{ident}", headers=_HTML, timeout=30)
            desc = strip_html(_description(page.text))
        except requests.RequestException:
            desc = ""
        time.sleep(0.1)
        pay = None
        if job.get("minPay") and job.get("maxPay"):
            pay = [int(job["minPay"]), int(job["maxPay"]), "USD", "year"]
        out.append({
            **base,
            "p": "walmart",
            "id": ident,
            "t": title,
            "loc": places,
            "ctry": "",
            "rem": False,
            "at": posted_at(job.get("jobPostingStartDate")),
            "url": f"https://careers.walmart.com/us/en/jobs/{ident}",
            "pay": pay or pay_from_text(desc),
            "yoe": yoe_snippets(desc),
            "desc": desc,
        })
    return out
