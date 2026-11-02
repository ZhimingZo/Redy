"""Boards for the traditional companies whose career site hid the ATS.

Each entry was read off the company's own careers page, or off the public
Workday host that page links to. `python -m crawler --house` writes the
registry and merges the jobs into the traditional track.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import normalize, tech
from .sources import bestbuy, fedex, goldman, mcdonalds, phenom, walmart
from .sources.custom import _fetch_radancy, _fetch_successfactors, fetch_pcsx
from .sources.workday import fetch as workday_fetch

log = logging.getLogger("crawler")

# ticker -> fields merged onto data/traditional_companies.json
KNOWN = {
    "PFE": {"ats": "workday", "slug": "pfizer.wd1.myworkdayjobs.com", "site": "PfizerCareers", "sites": ["PfizerCareers"], "careers": "https://www.pfizer.com/about/careers"},
    "COF": {"ats": "workday", "slug": "capitalone.wd12.myworkdayjobs.com", "site": "Capital_One", "sites": ["Capital_One"], "careers": "https://www.capitalonecareers.com/"},
    "MS": {"ats": "workday", "slug": "ms.wd5.myworkdayjobs.com", "site": "External", "sites": ["External"], "careers": "https://www.morganstanley.com/careers"},
    "BX": {"ats": "workday", "slug": "blackstone.wd1.myworkdayjobs.com", "site": "blackstone_Careers", "sites": ["blackstone_Careers"], "careers": "https://www.blackstone.com/careers/"},
    "CMCSA": {"ats": "workday", "slug": "comcast.wd115.myworkdayjobs.com", "site": "Comcast_Careers", "sites": ["Comcast_Careers"], "careers": "https://jobs.comcast.com/"},
    "HD": {"ats": "workday", "slug": "homedepot.wd5.myworkdayjobs.com", "site": "CareerDepot", "sites": ["CareerDepot"], "careers": "https://careers.homedepot.com/"},
    "WFC": {"ats": "workday", "slug": "wf.wd1.myworkdayjobs.com", "site": "WellsFargoJobs", "sites": ["WellsFargoJobs"], "careers": "https://www.wellsfargojobs.com/"},
    "EBAY": {"ats": "workday", "slug": "ebay.wd5.myworkdayjobs.com", "site": "apply", "sites": ["apply"], "careers": "https://careers.ebayinc.com/"},
    "GE": {"ats": "workday", "slug": "geaerospace.wd5.myworkdayjobs.com", "site": "GE_ExternalSite", "sites": ["GE_ExternalSite"], "careers": "https://careers.geaerospace.com/"},
    "GM": {"ats": "workday", "slug": "generalmotors.wd5.myworkdayjobs.com", "site": "Careers_GM", "sites": ["Careers_GM"], "careers": "https://search-careers.gm.com/"},
    "CAT": {"ats": "workday", "slug": "cat.wd5.myworkdayjobs.com", "site": "CaterpillarCareers", "sites": ["CaterpillarCareers"], "careers": "https://careers.caterpillar.com/"},
    "BLK": {"ats": "workday", "slug": "blackrock.wd1.myworkdayjobs.com", "site": "BlackRock_Professional", "sites": ["BlackRock_Professional"], "careers": "https://careers.blackrock.com/search-jobs"},
    "PG": {"ats": "workday", "slug": "pg.wd5.myworkdayjobs.com", "site": "1000", "sites": ["1000"], "careers": "https://www.pgcareers.com/"},
    "LOW": {"ats": "workday", "slug": "lowes.wd5.myworkdayjobs.com", "site": "LWS_External_CS", "sites": ["LWS_External_CS"], "careers": "https://talent.lowes.com/"},
    "DIS": {"ats": "workday", "slug": "disney.wd5.myworkdayjobs.com", "site": "disneycareer", "sites": ["disneycareer"], "careers": "https://join.disneycareers.com/"},
    "CVS": {"ats": "workday", "slug": "cvshealth.wd1.myworkdayjobs.com", "site": "CVS_Health_Careers", "sites": ["CVS_Health_Careers"], "careers": "https://jobs.cvshealth.com/"},
    "LLY": {"ats": "phenom", "slug": "https://careers.lilly.com/us/en/search-results", "careers": "https://careers.lilly.com/us/en"},
    "UPS": {"ats": "phenom", "slug": "https://www.jobs-ups.com/global/en/search-results", "careers": "https://www.jobs-ups.com/"},
    "UAL": {"ats": "phenom", "slug": "https://careers.united.com/us/en/search-results", "careers": "https://careers.united.com/"},
    "LUV": {"ats": "phenom", "slug": "https://careers.southwestair.com/us/en/search-results", "careers": "https://careers.southwestair.com/"},
    "GS": {"ats": "goldman", "slug": "higher.gs.com", "careers": "https://higher.gs.com/results"},
    "WMT": {"ats": "walmart", "slug": "careers.walmart.com", "careers": "https://careers.walmart.com/"},
    "FDX": {"ats": "fedex", "slug": "careers.fedex.com", "careers": "https://careers.fedex.com/jobs"},
    "BBY": {"ats": "bestbuy", "slug": "jobs.bestbuy.com", "careers": "https://jobs.bestbuy.com/bby"},
    "MCD": {"ats": "mcdonalds", "slug": "careers.mcdonalds.com", "careers": "https://careers.mcdonalds.com/jobs"},
    "DXCM": {"ats": "pcsx", "slug": "https://careers.dexcom.com", "pcsx": "dexcom.com", "key": "dexcom", "careers": "https://careers.dexcom.com/careers"},
    "DE": {"ats": "pcsx", "slug": "https://careers.deere.com", "pcsx": "johndeere.com", "key": "deere", "careers": "https://careers.deere.com/careers"},
    "BSX": {"ats": "pcsx", "slug": "https://bostonscientific.eightfold.ai", "pcsx": "bostonscientific.com", "key": "boston", "careers": "https://bostonscientific.eightfold.ai/careers"},
    "CMG": {"ats": "radancy", "slug": "https://jobs.chipotle.com", "key": "chipotle", "queries": ("engineer", "developer", "product manager", "scientist", "architect"), "careers": "https://jobs.chipotle.com/"},
    "HSY": {"ats": "successfactors", "slug": "https://careers.thehersheycompany.com", "key": "hershey", "careers": "https://careers.thehersheycompany.com/"},
    "KDP": {"ats": "radancy", "slug": "https://careers.keurigdrpepper.com", "key": "keurig", "careers": "https://careers.keurigdrpepper.com/en"},
    "LHX": {"ats": "radancy", "slug": "https://careers.l3harris.com", "key": "l3harris", "careers": "https://careers.l3harris.com/en"},
    "UNH": {"ats": "radancy", "slug": "https://careers.unitedhealthgroup.com", "key": "unitedhealth", "queries": ("engineer", "developer", "product manager", "scientist", "architect"), "careers": "https://careers.unitedhealthgroup.com/search-jobs"},
    "DHR": {"ats": "phenom", "slug": "https://jobs.danaher.com/global/en/search-results", "careers": "https://jobs.danaher.com/global/en"},
    "TMO": {"ats": "phenom", "slug": "https://jobs.thermofisher.com/global/en/search-results", "careers": "https://jobs.thermofisher.com/global/en"},
    "ETN": {"ats": "pcsx", "slug": "https://eaton.eightfold.ai", "pcsx": "eaton.com", "key": "eaton", "careers": "https://eaton.eightfold.ai/careers"},
    "UNP": {"ats": "successfactors", "slug": "https://up.jobs", "key": "unionpacific", "careers": "https://up.jobs/"},
    "LMT": {"ats": "pcsx", "slug": "https://lockheedmartin.eightfold.ai", "pcsx": "lockheedmartin.com", "key": "lockheed", "careers": "https://lockheedmartin.eightfold.ai/careers"},
}


def apply_known(path: str = "data/traditional_companies.json") -> int:
    """Record the boards. Leaves every other company alone."""
    doc = tech.load_registry(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    for company in doc["companies"]:
        fields = KNOWN.get(company.get("ticker"))
        if not fields:
            continue
        company.update({k: v for k, v in fields.items() if k not in {"key", "queries"}})
        company["error"] = None
        company["checked"] = now
        n += 1
    tech.save_registry(path, doc)
    log.info("recorded %s traditional boards", n)
    return n


def _base(company: dict) -> dict:
    return {
        "co": company["name"],
        "cslug": company.get("site") or company.get("slug") or company.get("domain"),
        "dom": company.get("domain"),
        "src": "traditional",
    }


def rows_for(fields: dict, base: dict) -> list[dict]:
    """One known traditional board. Callers decide what an empty list means."""
    kind = fields["ats"]
    if kind == "workday":
        return workday_fetch(fields["slug"], fields["site"], base)
    if kind == "phenom":
        return phenom.fetch(fields["slug"], base)
    if kind == "goldman":
        return goldman.fetch(base)
    if kind == "walmart":
        return walmart.fetch(base)
    if kind == "fedex":
        return fedex.fetch(base)
    if kind == "bestbuy":
        return bestbuy.fetch(base)
    if kind == "mcdonalds":
        return mcdonalds.fetch(base)
    if kind == "pcsx":
        return fetch_pcsx(fields["key"], fields["slug"], fields["pcsx"])
    if kind == "radancy":
        return _fetch_radancy(fields["key"], fields["slug"], tuple(fields.get("queries") or ()))
    if kind == "successfactors":
        return _fetch_successfactors(fields["key"], fields["slug"])
    return []


def crawl_house(path: str = "data/traditional_companies.json", out: str = "web/data/traditional.json", only: set[str] | None = None) -> int:
    apply_known(path)
    doc = tech.load_registry(path)
    by_ticker = {c.get("ticker"): c for c in doc["companies"]}
    raws: list[dict] = []
    for ticker, fields in KNOWN.items():
        company = by_ticker.get(ticker)
        if not company:
            log.warning("missing %s", ticker)
            continue
        if only and ticker not in only:
            continue
        try:
            found = rows_for(fields, _base(company))
        except Exception as e:
            log.warning("%s failed: %s", company.get("name"), str(e)[:160])
            found = []
        log.info("%s kept %s", company.get("name"), len(found))
        raws.extend(found)
    fresh = normalize.build(raws, datetime.now(timezone.utc))
    for job in fresh["jobs"]:
        job["track"] = "traditional"
    added = tech._merge(out, fresh)
    log.info("house added %s traditional jobs (%s kept after filters)", added, len(fresh["jobs"]))
    return added


_OPEN = ("DXCM", "DE", "BSX", "CMG", "HSY", "KDP", "LHX", "UNH", "DHR", "TMO", "ETN", "UNP", "LMT")


def crawl_open() -> int:
    """The boards whose public search already matches a reader we have. One company at a time."""
    added = 0
    for ticker in _OPEN:
        added += crawl_house(only={ticker})
    added += tech.crawl_uber()
    log.info("open added %s jobs", added)
    return added
