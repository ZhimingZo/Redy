"""Best Buy jobs run on a ServiceNow career portal.

The anonymous page embeds `g_ck`. Posting that token to the public job-search
widget returns US roles; the job widget returns the description and pay.
The widget ids are the ones the careers page itself requests.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

import requests

from ..classify import classify_family, level_from_title, us_locations
from .ats import pay_from_text, strip_html, yoe_snippets

log = logging.getLogger("crawler")

_ORIGIN = "https://jobs.bestbuy.com"
_SEARCH = "dd786d721b71b010b4c011f18c4bcb87"
_DETAIL = "a83a90ff1bbd3410b4c011f18c4bcb60"
_QUERIES = ("engineer", "developer", "product manager", "software")
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
_TIMEOUT = 30


def session_token(html: str) -> str:
    match = re.search(r"g_ck\s*=\s*'([^']+)'", html or "")
    return match.group(1) if match else ""


def job_rows(data: dict) -> list[dict]:
    """Job properties from the widget payload. They sit on a GeoJSON feature."""
    rows: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("auto_req_id") and node.get("title"):
                rows.append(node)
                return
            props = node.get("properties")
            if isinstance(props, dict) and props.get("auto_req_id"):
                rows.append(props)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    payload = data or {}
    walk(payload.get("items"))
    if not rows:
        walk(payload.get("geoData"))
    return rows


def bestbuy_places(props: dict) -> list[str]:
    city = (props.get("city") or "").strip()
    state = (props.get("state") or "").strip()
    country = (props.get("country") or "").strip()
    bit = ", ".join(p for p in (city, state) if p)
    if country.lower() in ("united states", "usa", "us"):
        place = f"{bit}, United States" if bit else "United States"
    else:
        place = ", ".join(p for p in (bit, country) if p)
    places = [place] if place else []
    if str(props.get("remote_eligible") or "").lower() in ("true", "yes", "1"):
        places.append("Remote")
    return places


def posted_at(text: str | None) -> str | None:
    try:
        day = datetime.strptime((text or "").strip(), "%d-%b-%Y")
    except ValueError:
        return None
    return day.strftime("%Y-%m-%dT12:00:00+00:00")


def pay_range(record: dict) -> list | None:
    try:
        lo = int(float(record.get("pay_min")))
        hi = int(float(record.get("pay_max")))
    except (TypeError, ValueError):
        return None
    if lo <= 0 or hi < lo:
        return None
    unit = "year" if hi >= 10000 else "hour"
    return [lo, hi, record.get("pay_currency") or "USD", unit]


def keep(title: str, places: list[str]) -> bool:
    if not classify_family(title) or level_from_title(title) == "junior":
        return False
    return bool(us_locations(places))


def _result(session: requests.Session, token: str, widget: str, query: str, body: dict) -> dict:
    response = session.post(
        f"{_ORIGIN}/api/now/sp/widget/{widget}?{query}",
        json=body,
        headers={**_UA, "X-UserToken": token},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return response.json().get("result") or {}


def fetch(base: dict, session: requests.Session | None = None) -> list[dict]:
    sess = session or requests.Session()
    page = sess.get(
        f"{_ORIGIN}/bby?id=all_jobs&spa=1",
        headers={**_UA, "Accept": "text/html"},
        timeout=_TIMEOUT,
    )
    page.raise_for_status()
    token = session_token(page.text)
    if not token:
        raise ValueError("best buy page had no session token")
    options = _result(sess, token, _SEARCH, "country=US&id=all_jobs&s=req_id_num&spa=1", {}).get("options") or {}

    seen: set[str] = set()
    picked: list[dict] = []
    for query in _QUERIES:
        page_no = 0
        total = None
        while page_no < 20:
            opts = dict(options)
            opts["filters"] = {"country": "countrySTARTSWITHUS", "q": f"GOTO123TEXTQUERY321={query}"}
            opts["currentPage"] = page_no
            opts["current_page"] = page_no
            data = _result(
                sess,
                token,
                _SEARCH,
                f"country=US&id=all_jobs&l=&q={requests.utils.quote(query)}&r=&s=relevance&spa=1",
                {"action": "update_data", "options": opts, "sessionRotationTrigger": True},
            ).get("data") or {}
            rows = job_rows(data)
            if total is None:
                total = int(data.get("total_count") or 0)
            if not rows:
                break
            for props in rows:
                ident = str(props.get("auto_req_id") or "")
                title = re.sub(r"\s+", " ", props.get("title") or "").strip()
                if not ident or ident in seen:
                    continue
                seen.add(ident)
                places = bestbuy_places(props)
                if not keep(title, places):
                    continue
                picked.append({"id": ident, "t": title, "loc": places, "at": posted_at(props.get("last_updated"))})
            page_no += 1
            per = int(options.get("items_per_page") or 20)
            if total and page_no * per >= total:
                break
            time.sleep(0.15)
        log.info("bestbuy %s: %s listed", query, total)

    out = []
    for row in picked:
        record: dict = {}
        try:
            data = _result(sess, token, _DETAIL, f"id=job_details&req_id={row['id']}", {})
            record = data.get("job") or {}
        except requests.RequestException:
            record = {}
        time.sleep(0.1)
        text = strip_html(record.get("job_description_dv") or "")
        out.append({
            **base,
            "p": "bestbuy",
            "id": row["id"],
            "t": row["t"],
            "loc": row["loc"],
            "ctry": "",
            "rem": any(re.search(r"remote", p, re.I) for p in row["loc"]),
            "at": row["at"],
            "url": f"{_ORIGIN}/bby?id=job_details&req_id={row['id']}",
            "pay": pay_range(record) or pay_from_text(text),
            "yoe": yoe_snippets(text),
            "desc": text,
        })
    log.info("bestbuy kept %s", len(out))
    return out
