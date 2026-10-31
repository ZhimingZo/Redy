"""Company discovery: which startups to crawl, and where their jobs live.

- a16z: jobs.a16z.com/companies embeds every portfolio company (stage, headcount, domain);
  each company's page on jobs.a16z.com links to its own ATS board, which we then crawl directly.
- YC:   the open yc-oss dataset lists hiring companies; each company's public YC jobs page
  (ycombinator.com/companies/<slug>/jobs) carries structured postings (role, min experience,
  salary, visa) in its data-page JSON.
"""
from __future__ import annotations

import html
import json
import re

import requests

from .ats import UA, TIMEOUT

STARTUP_STAGES = {"Seed", "Series A", "Series B", "Series C", "Series D", "Series E", "Series F", "Growth", "Venture", None}
_ATS = re.compile(r"(jobs\.ashbyhq\.com|boards\.greenhouse\.io|job-boards\.greenhouse\.io|jobs\.lever\.co)/([A-Za-z0-9_.\-]+)")
_A16Z_CO = re.compile(
    r'\{"id":"[a-z0-9]+","slug":"([^"]+)","name":"([^"]+)","logoUrl":"([^"]*)","domain":"([^"]*)"[\s\S]*?"location":("[^"]*"|null)'
    r'[\s\S]*?"headcount":(\d+|null),"markets":\[([^\]]*)\],"stage":("[^"]*"|null),"employeeBand":("[^"]*"|null),"jobCount":(\d+)\}'
)


def a16z_companies(session: requests.Session) -> list[dict]:
    raw = session.get("https://jobs.a16z.com/companies", headers=UA, timeout=TIMEOUT).text
    s = raw.replace("\\\\", "\x01").replace('\\"', '"').replace("\x01", "\\")
    out, seen = [], set()
    for m in _A16Z_CO.finditer(s):
        if m.group(1) in seen:
            continue
        seen.add(m.group(1))
        out.append({"slug": m.group(1), "name": m.group(2), "logo": m.group(3), "domain": m.group(4),
                    "headcount": None if m.group(6) == "null" else int(m.group(6)),
                    "markets": [x.strip('"') for x in m.group(7).split(",") if x],
                    "stage": json.loads(m.group(8)), "jobCount": int(m.group(10))})
    return out


def a16z_board(session: requests.Session, slug: str) -> str | None:
    """Most-linked ATS board on the company's a16z jobs page, e.g. 'jobs.ashbyhq.com/openai'."""
    page = session.get(f"https://jobs.a16z.com/jobs/{slug}", headers=UA, timeout=TIMEOUT).text
    counts: dict[str, int] = {}
    for host, board in _ATS.findall(page):
        if board == "embed":
            continue
        k = host.replace("job-boards.", "boards.") + "/" + board.lower()
        counts[k] = counts.get(k, 0) + 1
    return max(counts, key=counts.get) if counts else None


def yc_hiring_companies(session: requests.Session) -> list[dict]:
    cos = session.get("https://yc-oss.github.io/api/companies/hiring.json", headers=UA, timeout=60).json()
    us = re.compile(r"United States|America|Remote", re.I)
    return [c for c in cos if not c.get("regions") or any(us.search(r) for r in c["regions"])]


def yc_jobs(session: requests.Session, company: dict) -> list[dict]:
    page = session.get(f"https://www.ycombinator.com/companies/{company['slug']}/jobs", headers=UA, timeout=TIMEOUT).text
    m = re.search(r'data-page="([^"]+)"', page)
    if not m:
        return []
    props = json.loads(html.unescape(m.group(1))).get("props", {})
    co = props.get("company") or {}
    dom = re.sub(r"^https?://(www\.)?", "", company.get("website") or "").split("/")[0]
    out = []
    for j in props.get("jobPostings") or []:
        if j.get("role") not in ("eng", "product"):
            continue
        out.append({"co": co.get("name") or company["name"], "cslug": company["slug"], "dom": dom, "stage": company.get("stage"),
                    "hc": company.get("team_size"), "batch": company.get("batch"), "ind": company.get("industry"),
                    "logo": company.get("small_logo_thumb_url"), "src": "yc", "p": "ycombinator", "id": str(j["id"]),
                    "t": j.get("title", ""), "dept": j.get("prettyRole"), "role": j.get("role"), "loc": [j.get("location") or ""],
                    "rem": "remote" in (j.get("location") or "").lower(), "ago": j.get("createdAt"),
                    "url": "https://www.ycombinator.com" + j.get("url", ""), "sal": j.get("salaryRange"), "exp": j.get("minExperience"),
                    "visa": j.get("visa")})
    return out
