"""Public ATS job-board adapters. Each returns raw records in the shared shape (see normalize.py)."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import requests

UA = {"User-Agent": "RedyBot/0.1 (+https://redy.fyi; job board for senior engineers)"}
TIMEOUT = 25

_YOE_SNIP = re.compile(r"[^.\n]{0,80}\b\d{1,2}\s*\+?\s*(?:-|–|to)?\s*\d{0,2}\+?\s*(?:years|yrs)[^.\n]{0,60}", re.I)
_PAY = re.compile(r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3}\s?[kK])\s*(?:-|–|to)\s*\$?\s?(\d{2,3}(?:,\d{3})+|\d{2,3}\s?[kK])")


def strip_html(s: str | None) -> str:
    s = html.unescape(html.unescape(s or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))


def yoe_snippets(text: str | None) -> list[str]:
    return [m.group(0).strip()[:160] for m in _YOE_SNIP.finditer(text or "")][:3]


def pay_from_text(text: str) -> list | None:
    m = _PAY.search(text or "")
    if not m:
        return None

    def n(s: str) -> int:
        s = re.sub(r"[,\s]", "", s)
        return int(s[:-1]) * 1000 if s[-1] in "kK" else int(s)

    return [n(m.group(1)), n(m.group(2)), "USD", "year"]


def ashby(slug: str, base: dict) -> list[dict]:
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "true"}, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        comp = next((c for c in (j.get("compensation") or {}).get("summaryComponents", []) if c.get("compensationType") == "Salary" and c.get("minValue")), None)
        out.append({**base, "p": "ashby", "id": j["id"], "t": j["title"], "dept": " / ".join(filter(None, [j.get("department"), j.get("team")])),
                    "loc": [j.get("location")] + [x.get("location") for x in j.get("secondaryLocations") or []],
                    "ctry": ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry", ""),
                    "rem": j.get("isRemote") or j.get("workplaceType") == "Remote", "at": j.get("publishedAt"), "url": j.get("jobUrl"),
                    "pay": [comp["minValue"], comp.get("maxValue"), comp.get("currencyCode"), comp.get("interval")] if comp else None,
                    "yoe": yoe_snippets(j.get("descriptionPlain")), "desc": j.get("descriptionPlain") or ""})
    return out


def greenhouse_posting_places(job: dict) -> list[str]:
    """Where Cloudflare's careers page reads a city: metadata, then office names, then the location field."""
    for item in job.get("metadata") or []:
        if (item.get("name") or "").strip().lower() != "job posting location":
            continue
        value = item.get("value")
        parts = value if isinstance(value, list) else [value]
        places = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
        if places:
            return places
    offices = [
        office.get("name").strip()
        for office in job.get("offices") or []
        if isinstance(office, dict) and (office.get("name") or "").strip()
    ]
    if offices:
        return offices
    name = ((job.get("location") or {}).get("name") or "").strip()
    return [name] if name else []


def greenhouse_posted(job: dict) -> str | None:
    """Last change on the board. first_published stays put while an open req is edited for years."""
    return job.get("updated_at") or job.get("first_published")


def greenhouse(slug: str, base: dict, timeout: int = TIMEOUT, places=None) -> list[dict]:
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}, headers=UA, timeout=timeout)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        txt = strip_html(j.get("content"))
        loc = places(j) if places else [(j.get("location") or {}).get("name", "")]
        out.append({**base, "p": "greenhouse", "id": str(j["id"]), "t": j["title"], "dept": " / ".join(d["name"] for d in j.get("departments") or []),
                    "loc": loc, "rem": any("remote" in part.lower() for part in loc), "at": greenhouse_posted(j),
                    "url": j.get("absolute_url"), "pay": pay_from_text(txt), "yoe": yoe_snippets(txt), "desc": txt})
    return out


def lever(slug: str, base: dict) -> list[dict]:
    r = requests.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json() or []:
        cat = j.get("categories") or {}
        sr = j.get("salaryRange") or {}
        body = (j.get("descriptionPlain") or "") + " " + " ".join(strip_html(l.get("content")) for l in j.get("lists") or [])
        out.append({**base, "p": "lever", "id": j["id"], "t": j["text"], "dept": " / ".join(filter(None, [cat.get("department"), cat.get("team")])),
                    "loc": cat.get("allLocations") or [cat.get("location")], "ctry": j.get("country"), "rem": j.get("workplaceType") == "remote",
                    "at": datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).isoformat() if j.get("createdAt") else None,
                    "url": j.get("hostedUrl"), "pay": [sr["min"], sr.get("max"), sr.get("currency"), sr.get("interval")] if sr.get("min") else None,
                    "yoe": yoe_snippets(body), "desc": body})
    return out


def smartrecruiters_row(slug: str, job: dict) -> dict:
    """One posting from the public list. Description is filled in later."""
    loc = job.get("location") or {}
    if not isinstance(loc, dict):
        loc = {"fullLocation": str(loc)}
    place = loc.get("fullLocation") or ", ".join(
        part for part in (loc.get("city"), loc.get("region"), loc.get("country")) if part
    )
    country = (loc.get("country") or "").lower()
    if country in {"us", "usa"} and place and not re.search(r"united states|\busa\b", place, re.I):
        place = f"{place}, United States"
    ident = str(job.get("id") or "")
    return {
        "id": ident,
        "t": re.sub(r"\s+", " ", job.get("name") or "").strip(),
        "loc": [place] if place else [],
        "ctry": loc.get("country") or "",
        "rem": bool(loc.get("remote")),
        "at": job.get("releasedDate"),
        "url": f"https://jobs.smartrecruiters.com/{slug}/{ident}",
        "desc": "",
    }


def _smartrecruiters_desc(payload: dict) -> str:
    ad = payload.get("jobAd") or {}
    if not isinstance(ad, dict):
        return ""
    parts = []
    for section in ad.get("sections") or []:
        if isinstance(section, dict):
            parts.append(section.get("text") or "")
        elif isinstance(section, str):
            parts.append(section)
    return strip_html(" ".join(parts))


def smartrecruiters(slug: str, base: dict) -> list[dict]:
    """Public postings. The description is requested only for a US eng/PM title."""
    from ..classify import classify_family, level_from_title, us_locations

    out = []
    offset = 0
    total = 1
    while offset < total and offset < 5000:
        response = requests.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
            params={"limit": 100, "offset": offset},
            headers=UA,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        total = int(payload.get("totalFound") or 0)
        postings = payload.get("content") or []
        if not postings:
            break
        for job in postings:
            row = smartrecruiters_row(slug, job)
            if not row["id"] or not row["t"]:
                continue
            if not classify_family(row["t"]) or level_from_title(row["t"]) == "junior":
                continue
            if not us_locations(row["loc"], row["ctry"]):
                continue
            try:
                detail = requests.get(
                    f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{row['id']}",
                    headers=UA,
                    timeout=TIMEOUT,
                )
                detail.raise_for_status()
                row["desc"] = _smartrecruiters_desc(detail.json())
            except (requests.RequestException, ValueError):
                row["desc"] = ""
            text = row["desc"]
            out.append({
                **base,
                "p": "smartrecruiters",
                **row,
                "pay": pay_from_text(text),
                "yoe": yoe_snippets(text),
            })
        offset += len(postings)
    return out


ADAPTERS = {"jobs.ashbyhq.com": ashby, "boards.greenhouse.io": greenhouse, "jobs.lever.co": lever}
