"""Public ATS job-board adapters. Each returns raw records in the shared shape (see normalize.py)."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import requests

UA = {"User-Agent": "ReedyBot/0.1 (+https://github.com/; job board for senior engineers)"}
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
                    "yoe": yoe_snippets(j.get("descriptionPlain"))})
    return out


def greenhouse(slug: str, base: dict) -> list[dict]:
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        txt = strip_html(j.get("content"))
        loc = (j.get("location") or {}).get("name", "")
        out.append({**base, "p": "greenhouse", "id": str(j["id"]), "t": j["title"], "dept": " / ".join(d["name"] for d in j.get("departments") or []),
                    "loc": [loc], "rem": "remote" in loc.lower(), "at": j.get("first_published") or j.get("updated_at"),
                    "url": j.get("absolute_url"), "pay": pay_from_text(txt), "yoe": yoe_snippets(txt)})
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
                    "yoe": yoe_snippets(body)})
    return out


ADAPTERS = {"jobs.ashbyhq.com": ashby, "boards.greenhouse.io": greenhouse, "jobs.lever.co": lever}
