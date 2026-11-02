"""Turn raw source records into the site's compact jobs.json.

Raw records (from the live crawler or a browser snapshot) share one shape:
  co, cslug, dom, stage, hc, logo, src ('a16z'|'yc'), p (provider), id, t (title), dept,
  loc [..], ctry, rem, url, and either
  - ATS fields: at (ISO date), pay [min, max, currency, interval], yoe [snippets]
  - YC fields:  ago, sal, exp, visa, role, batch, ind
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from . import classify as C
from . import locations as L

MIN_LEVELS = {"mid", "senior", "staff", "mgmt", "unk"}  # 'junior' never ships


def desc_hash(text: str | None) -> str | None:
    """Stable id for a job description. Whitespace-only edits do not count."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) < 80:
        return None
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _posted(raw: dict, crawled_at: datetime) -> str | None:
    if raw.get("at"):
        try:
            return datetime.fromisoformat(raw["at"].replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
        except ValueError:
            return None
    d = C.days_from_ago(raw.get("ago"))
    return (crawled_at - timedelta(days=d)).date().isoformat() if d is not None else None


def normalize_job(raw: dict, crawled_at: datetime) -> dict | None:
    title = re.sub(r"\s+", " ", raw.get("t") or "").strip()
    fam = C.classify_family(title, raw.get("role"))
    if not fam:
        return None
    if not C.us_locations(raw.get("loc"), raw.get("ctry")):
        return None
    locs, remote = L.pretty_locations(raw.get("loc"))
    if not locs:
        return None  # only non-US places left after cleanup (e.g. "Remote (IN)")

    if raw.get("src") == "yc":
        yoe, new_grads_ok = C.parse_yc_experience(raw.get("exp"))
        pay = C.parse_yc_salary(raw.get("sal"))
        v = (raw.get("visa") or "").lower()
        visa = "sponsor" if "will sponsor" in v else "none" if "only" in v else "open" if "not required" in v else None
    else:
        yoe, new_grads_ok = C.parse_yoe(raw.get("yoe")), False
        p = raw.get("pay")
        pay = C.normalize_pay(p[0], p[1], p[2], p[3]) if p else None
        visa = None

    level = C.classify_level(title, yoe, new_grads_ok)
    if level not in MIN_LEVELS:
        return None

    key = f"{raw.get('src')}:{raw.get('p')}:{raw.get('cslug')}:{raw.get('id')}"
    out = {
        "id": hashlib.sha1(key.encode()).hexdigest()[:10],
        "t": title,
        "lvl": level,
        "fam": fam,
        "sub": C.classify_subfamily(title, fam),
        "yoe": yoe,
        "loc": locs[:6],
        "rem": remote or bool(raw.get("rem")),
        "pay": pay,
        "at": _posted(raw, crawled_at),
        "url": raw.get("url"),
        "visa": visa,
        "src": raw.get("p"),
        "_co": raw,  # stripped after company merge
    }
    hashed = desc_hash(raw.get("desc"))
    if hashed:
        out["dh"] = hashed
    return out


def _company_key(raw: dict) -> str:
    dom = (raw.get("dom") or "").lower().removeprefix("www.")
    return dom or re.sub(r"[^a-z0-9]", "", (raw.get("co") or "").lower())


def build(raw_records: list[dict], crawled_at: datetime) -> dict:
    companies: dict[str, dict] = {}
    jobs, seen = [], {}
    for raw in raw_records:
        j = normalize_job(raw, crawled_at)
        if not j:
            continue
        r = j.pop("_co")
        ck = _company_key(r)
        backer = {"yc": "YC", "a16z": "a16z"}.get(r.get("src"))
        c = companies.setdefault(ck, {"n": r.get("co"), "d": r.get("dom"), "backers": [], "stage": None, "sb": "Unknown",
                                      "hc": None, "logo": r.get("logo"), "batch": None, "ind": None})
        if backer and backer not in c["backers"]:
            c["backers"].append(backer)
        # a16z stage labels are more precise than YC's Early/Growth
        if r.get("stage") and (c["stage"] is None or backer == "a16z"):
            c["stage"], c["sb"] = r["stage"], C.stage_bucket(r["stage"], backer)
        c["hc"] = c["hc"] or r.get("hc")
        c["batch"] = c["batch"] or C.short_batch(r.get("batch"))
        c["ind"] = c["ind"] or r.get("ind") or (", ".join(r.get("mk") or [])[:40] or None)
        for k in ("bio", "yr", "ceo", "founders"):
            if r.get(k) and not c.get(k):
                c[k] = r[k]
        # one row per (company, title): merge roles posted once per city or listed by both sources
        dk = (ck, re.sub(r"[^a-z0-9]+", " ", j["t"].lower()).strip())
        if dk in seen:
            first = seen[dk]
            first["loc"] = list(dict.fromkeys(first["loc"] + j["loc"]))[:8]
            first["rem"] = first["rem"] or j["rem"]
            first["pay"] = first["pay"] or j["pay"]
            first["at"] = max(first["at"] or "", j["at"] or "") or None
            continue
        j["c"] = ck
        seen[dk] = j
        jobs.append(j)

    jobs.sort(key=lambda j: j["at"] or "", reverse=True)
    return {"generated_at": crawled_at.isoformat(timespec="seconds"), "companies": companies, "jobs": jobs}


def note_changes(previous: list, jobs: list) -> None:
    """Mark a row when the same posting's description hash changed since the last file."""
    old = {job.get("id"): job.get("dh") for job in previous if job.get("id")}
    for job in jobs:
        prev = old.get(job.get("id"))
        new = job.get("dh")
        if prev and new and prev != new:
            job["chg"] = True
        elif prev and new and prev == new:
            job.pop("chg", None)


def write(data: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
