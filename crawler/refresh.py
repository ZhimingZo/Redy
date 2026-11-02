"""Rebuild the Tech and Traditional boards from the readers already in use.

`crawl_boards` replaces a whole track with Greenhouse, Lever, and Ashby only.
These two tracks also hold Workday, Oracle, and company-specific pages, so the
daily job never calls it. A company is replaced only when its pull succeeds.
A failed pull, or a paginated pull that comes back less than half the size,
leaves yesterday's rows in place. The live file is swapped only after the new
board is still at least half as large.
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import house, normalize, tech
from .sources import ats
from .sources.ats import greenhouse_posting_places

log = logging.getLogger("crawler")

# The Ashby slug "analog" is a different board, not Analog Devices.
SKIP_DOMAINS = frozenset({"analog.com"})

# Registry rows for these point at app.eightfold.ai or a sandbox host.
# The career sites that actually answer are the ones already crawled.
EIGHTFOLD = (
    ("qualcomm", "https://careers.qualcomm.com", "qualcomm.com"),
    ("appliedmaterials", "https://careers.appliedmaterials.com", "appliedmaterials.com"),
    ("hp", "https://apply.hp.com", "hp.com"),
    ("autodesk", "https://jobs.autodesk.com", "autodesk.com"),
    ("micron", "https://careers.micron.com", "micron.com"),
    ("lam", "https://careers.lamresearch.com", "lamresearch.com"),
)

CUSTOM = (
    ("apple", "apple.com"),
    ("amazon", "amazon.com"),
    ("google", "google.com"),
    ("netflix", "netflix.com"),
    ("meta", "meta.com"),
    ("tesla", "tesla.com"),
    ("microsoft", "microsoft.com"),
    ("salesforce", "salesforce.com"),
    ("paypal", "paypal.com"),
    ("intuit", "intuit.com"),
    ("synopsys", "synopsys.com"),
    ("supermicro", "supermicro.com"),
    ("amd", "amd.com"),
    ("netapp", "netapp.com"),
)


def posting_places_for(domain: str):
    """Cloudflare's location field says Hybrid or Distributed. The city is metadata."""
    if domain == "cloudflare.com":
        return greenhouse_posting_places
    return None


def keep_pull(new_count: int, old_count: int, trust_empty: bool) -> bool:
    """Whether this pull may replace the rows already on file."""
    if new_count == 0:
        return trust_empty
    if not trust_empty and old_count >= 20 and new_count * 2 < old_count:
        return False
    return True


def enough(new_count: int, old_count: int) -> bool:
    """The published file has to stay in the same range. A wiped file does not."""
    if old_count == 0:
        return new_count > 0
    return new_count * 2 >= old_count


def compose_board(old: dict, fresh: dict, replaced: set[str]) -> dict:
    """Take fresh rows for replaced domains. Every other domain stays as it was."""
    jobs = [job for job in old.get("jobs") or [] if job.get("c") not in replaced]
    jobs.extend(job for job in fresh.get("jobs") or [] if job.get("c") in replaced)
    jobs.sort(key=lambda job: job.get("at") or "", reverse=True)
    normalize.note_changes(
        old.get("jobs") or [],
        [job for job in jobs if job.get("c") in replaced],
    )
    companies = dict(old.get("companies") or {})
    for domain in replaced:
        companies.pop(domain, None)
    for domain, company in (fresh.get("companies") or {}).items():
        if domain not in replaced:
            continue
        merged = dict((old.get("companies") or {}).get(domain) or {})
        for field, value in company.items():
            if value not in (None, "", [], {}):
                merged[field] = value
        companies[domain] = merged
    return {
        "generated_at": fresh.get("generated_at") or old.get("generated_at"),
        "companies": companies,
        "jobs": jobs,
    }


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {"generated_at": None, "companies": {}, "jobs": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _publish(path: str, doc: dict, old_count: int) -> bool:
    new_count = len(doc.get("jobs") or [])
    if not enough(new_count, old_count):
        log.error("refusing %s: %d jobs would replace %d", path, new_count, old_count)
        return False
    tmp = path + ".tmp"
    normalize.write(doc, tmp)
    os.replace(tmp, path)
    log.info("wrote %d jobs -> %s (was %d)", new_count, path, old_count)
    return True


def _base(company: dict, src: str) -> dict:
    return {
        "co": company.get("name"),
        "cslug": company.get("slug") or company.get("domain"),
        "dom": company.get("domain"),
        "src": src,
        "hc": company.get("employees") or None,
    }


def _pull(domain: str, trust_empty: bool, fn) -> tuple[str, list, bool]:
    try:
        rows = fn() or []
    except Exception as e:
        log.warning("%s failed: %s", domain, str(e)[:160])
        return domain, [], False
    return domain, rows, trust_empty


def _accept(domain: str, rows: list, trust_empty: bool, old_counts: dict[str, int], track: str, now: datetime) -> tuple[set[str], dict | None]:
    fresh = normalize.build(rows, now)
    for job in fresh["jobs"]:
        job["track"] = track
    if any(job.get("c") != domain for job in fresh["jobs"]):
        log.warning("%s pull named another company; keeping yesterday", domain)
        return set(), None
    count = len(fresh["jobs"])
    if not keep_pull(count, old_counts.get(domain, 0), trust_empty):
        log.warning("keeping %s (%d on file, pull kept %d)", domain, old_counts.get(domain, 0), count)
        return set(), None
    return {domain}, fresh


def _stack(fragments: list[dict], now: datetime) -> dict:
    companies: dict = {}
    jobs: list = []
    for fragment in fragments:
        companies.update(fragment.get("companies") or {})
        jobs.extend(fragment.get("jobs") or [])
    return {"generated_at": now.isoformat(timespec="seconds"), "companies": companies, "jobs": jobs}


def _refresh(path: str, track: str, pulls: list[tuple[str, list, bool]]) -> bool:
    old = _load(path)
    old_counts: dict[str, int] = {}
    for job in old.get("jobs") or []:
        if job.get("c"):
            old_counts[job["c"]] = old_counts.get(job["c"], 0) + 1
    now = datetime.now(timezone.utc)
    replaced: set[str] = set()
    fragments: list[dict] = []
    for domain, rows, trust_empty in pulls:
        domains, fragment = _accept(domain, rows, trust_empty, old_counts, track, now)
        replaced |= domains
        if fragment:
            fragments.append(fragment)
    if not replaced:
        log.error("%s: no company pulled successfully; leaving %s", track, path)
        return False
    doc = compose_board(old, _stack(fragments, now), replaced)
    log.info(
        "%s: %d companies replaced, %d left as they were",
        track,
        len(replaced),
        len(old_counts) - len(replaced & set(old_counts)),
    )
    return _publish(path, doc, len(old.get("jobs") or []))


def _pool(specs: list[tuple[str, bool, object]], workers: int) -> list[tuple[str, list, bool]]:
    if not specs:
        return []

    def run(spec):
        domain, trust_empty, fn = spec
        return _pull(domain, trust_empty, fn)

    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(run, specs))


def _registered(path: str, src: str, kinds: set[str], skip: set[str], trust_empty: bool) -> list[tuple[str, bool, object]]:
    doc = tech.load_registry(path)
    specs = []
    for company in doc["companies"]:
        domain = (company.get("domain") or "").lower().removeprefix("www.")
        kind = company.get("ats")
        if domain in skip or domain in SKIP_DOMAINS or kind not in kinds or not company.get("slug"):
            continue
        if kind == "workday" and not company.get("site"):
            continue
        base = _base(company, src)
        if kind == "greenhouse":
            places = posting_places_for(domain)
            specs.append((domain, trust_empty, lambda slug=company["slug"], base=base, places=places: ats.greenhouse(slug, base, timeout=120, places=places)))
        elif kind == "lever":
            specs.append((domain, trust_empty, lambda slug=company["slug"], base=base: ats.lever(slug, base)))
        elif kind == "ashby":
            specs.append((domain, trust_empty, lambda slug=company["slug"], base=base: ats.ashby(slug, base)))
        elif kind == "smartrecruiters":
            specs.append((domain, trust_empty, lambda slug=company["slug"], base=base: ats.smartrecruiters(slug, base)))
        elif kind == "workday":
            sites = company.get("sites") or [company["site"]]
            host = company["slug"]
            specs.append((domain, trust_empty, lambda host=host, sites=sites, base=base: _workday(host, sites, base)))
        elif kind == "oracle":
            specs.append((domain, trust_empty, lambda host=company["slug"], base=base: _oracle(host, base)))
    return specs


def _workday(host: str, sites: list, base: dict) -> list[dict]:
    from .sources.workday import fetch

    rows = []
    for site in sites:
        rows.extend(fetch(host, site, base))
    return rows


def _oracle(host: str, base: dict) -> list[dict]:
    from .sources.oracle import fetch

    return fetch(host, base)


def _custom_specs() -> list[tuple[str, bool, object]]:
    from .sources import custom

    fns = {
        "apple": custom.fetch_apple,
        "amazon": custom.fetch_amazon,
        "google": custom.fetch_google,
        "netflix": custom.fetch_netflix,
        "meta": custom.fetch_meta,
        "tesla": custom.fetch_tesla,
        "microsoft": custom.fetch_microsoft,
        "salesforce": custom.fetch_salesforce,
        "paypal": custom.fetch_paypal,
        "intuit": custom.fetch_intuit,
        "synopsys": custom.fetch_synopsys,
        "supermicro": custom.fetch_supermicro,
        "amd": custom.fetch_amd,
        "netapp": custom.fetch_netapp,
    }
    return [(domain, False, fns[name]) for name, domain in CUSTOM]


def _eightfold_specs() -> list[tuple[str, bool, object]]:
    from .sources.custom import fetch_pcsx

    return [
        (domain, False, lambda which=which, origin=origin, domain=domain: fetch_pcsx(which, origin, domain))
        for which, origin, domain in EIGHTFOLD
    ]


def _house_domains() -> set[str]:
    doc = tech.load_registry("data/traditional_companies.json")
    tickers = set(house.KNOWN)
    return {
        (company.get("domain") or "").lower().removeprefix("www.")
        for company in doc["companies"]
        if company.get("ticker") in tickers and company.get("domain")
    }


def _house_specs() -> list[tuple[str, bool, object]]:
    doc = tech.load_registry("data/traditional_companies.json")
    by_ticker = {company.get("ticker"): company for company in doc["companies"]}
    specs = []
    for ticker, fields in house.KNOWN.items():
        company = by_ticker.get(ticker)
        domain = (company or {}).get("domain") or ""
        domain = domain.lower().removeprefix("www.")
        if not domain:
            continue
        specs.append((domain, False, lambda fields=fields, company=company: house.rows_for(fields, house._base(company))))
    return specs


def refresh_tech(out: str = "web/data/tech.json") -> bool:
    """Pull every Tech reader onto a new board, then swap the file."""
    registry = "data/tech_companies.json"
    pulls = []
    pulls.extend(_pool(_registered(registry, "tech", {"greenhouse", "lever", "ashby", "smartrecruiters"}, set(), True), 8))
    pulls.extend(_pool(_registered(registry, "tech", {"workday"}, set(), False), 4))
    pulls.extend(_pool(_registered(registry, "tech", {"oracle"}, set(), False), 3))
    pulls.extend(_pool(_eightfold_specs(), 1))
    pulls.extend(_pool(_custom_specs(), 3))
    return _refresh(out, "tech", pulls)


def refresh_traditional(out: str = "web/data/traditional.json") -> bool:
    """Pull Traditional readers. House boards are not also read from the registry."""
    registry = "data/traditional_companies.json"
    skip = _house_domains()
    pulls = []
    pulls.extend(_pool(_house_specs(), 2))
    pulls.extend(_pool(_registered(registry, "traditional", {"greenhouse", "lever", "ashby", "smartrecruiters"}, skip, True), 8))
    pulls.extend(_pool(_registered(registry, "traditional", {"workday"}, skip, False), 4))
    pulls.extend(_pool(_registered(registry, "traditional", {"oracle"}, skip, False), 3))
    return _refresh(out, "traditional", pulls)


def refresh_tracks(which: str = "all") -> int:
    ok = True
    if which in ("tech", "all"):
        ok = refresh_tech() and ok
    if which in ("traditional", "all"):
        ok = refresh_traditional() and ok
    return 0 if ok else 1
