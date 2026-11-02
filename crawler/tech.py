"""Detect each tech company's ATS from its official site, then pull the boards we can read.

    python -m crawler --tech-list wikidata.json
    python -m crawler --tech
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from . import normalize
from .sources import ats
from .sources.careers import CRAWLABLE, career_links, classify_board, names_match, slug_from_domain

log = logging.getLogger("crawler")

BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}
_FETCH_TIMEOUT = (6, 12)
_API_TIMEOUT = 12


def load_registry(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_registry(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def write_list(src: str, dest: str, limit: int = 1000) -> int:
    raise SystemExit(
        "data/tech_companies.json is the tech list: US public tech companies and US unicorns. "
        "It is not built from a Wikidata dump."
    )


def _get(url: str) -> tuple[str, str]:
    r = requests.get(url, headers=BROWSER, timeout=_FETCH_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").lower()
    if ctype and "html" not in ctype and "text" not in ctype and "xml" not in ctype:
        return r.url, ""
    return r.url, r.text[:300_000]


def _fallbacks(website: str) -> list[str]:
    if "://" not in website:
        website = "https://" + website
    p = urlparse(website)
    origin = f"{p.scheme}://{p.netloc}"
    domain = p.netloc.lower().removeprefix("www.")
    return [origin + "/careers", origin + "/jobs", f"https://careers.{domain}", f"https://jobs.{domain}"]


def _probe(kind: str, slug: str, company: str) -> dict | None:
    slug = (slug or "").lower()
    if len(slug) < 2:
        return None
    try:
        if kind == "greenhouse":
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}", headers=ats.UA, timeout=_API_TIMEOUT)
            if r.status_code != 200:
                return None
            if not names_match(company, (r.json() or {}).get("name") or "", slug):
                return None
        elif kind == "lever":
            r = requests.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}, headers=ats.UA, timeout=_API_TIMEOUT)
            if r.status_code != 200 or not isinstance(r.json(), list):
                return None
        elif kind == "ashby":
            r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", headers=ats.UA, timeout=_API_TIMEOUT)
            if r.status_code != 200:
                return None
        else:
            return None
    except (requests.RequestException, ValueError):
        return None
    return {"ats": kind, "slug": slug}


def detect_company(company: dict) -> dict:
    website = company.get("website") or f"https://{company.get('domain')}"
    tried: set[str] = set()
    error = None

    def look(url: str) -> dict | None:
        nonlocal error
        if not url or url in tried:
            return None
        tried.add(url)
        try:
            final, text = _get(url)
        except requests.RequestException as e:
            error = str(e)[:120]
            return None
        hit = classify_board(final, text)
        if hit:
            hit["careers"] = final
            return hit
        return {"html": text, "final": final}

    home = look(website)
    if home and home.get("ats"):
        return home
    html = (home or {}).get("html") or ""
    final = (home or {}).get("final") or website
    links = career_links(html, final)
    pages = links[:2] if links else _fallbacks(website)[:2]
    for url in pages:
        hit = look(url)
        if hit and hit.get("ats"):
            return hit

    slug = slug_from_domain(company.get("domain") or "")
    # Two-letter guesses (hp, ea) collide too often to trust without a link on the official site.
    if len(slug) >= 3:
        for ats_name in ("greenhouse", "lever", "ashby"):
            hit = _probe(ats_name, slug, company.get("name") or "")
            if hit:
                hit["careers"] = f"https://{company.get('domain')}"
                hit["via"] = "api"
                return hit
    return {"ats": None, "slug": None, "careers": None, "error": error}


def detect_all(path: str, workers: int = 12) -> dict:
    doc = load_registry(path)
    pending = [c for c in doc["companies"] if not c.get("checked")]
    log.info("detecting ATS for %d/%d companies", len(pending), len(doc["companies"]))
    lock = threading.Lock()
    done = 0

    def one(company: dict) -> None:
        nonlocal done
        hit = detect_company(company)
        with lock:
            company.update(hit)
            company["checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            done += 1
            if done % 25 == 0 or done == len(pending):
                save_registry(path, doc)
                log.info("detected %d/%d", done, len(pending))

    if pending:
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(one, pending))
        save_registry(path, doc)
    counts: dict[str, int] = {}
    for c in doc["companies"]:
        counts[c.get("ats") or "unknown"] = counts.get(c.get("ats") or "unknown", 0) + 1
    log.info("ATS split: %s", ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
    return counts


def crawl_boards(path: str, out: str, workers: int = 8, src: str = "tech", track: str = "tech") -> dict:
    doc = load_registry(path)
    targets = [c for c in doc["companies"] if c.get("ats") in CRAWLABLE and c.get("slug")]
    log.info("crawling %d Greenhouse/Lever/Ashby boards", len(targets))
    fns = {"greenhouse": ats.greenhouse, "lever": ats.lever, "ashby": ats.ashby}

    def one(c: dict) -> list[dict]:
        base = {
            "co": c["name"], "cslug": c["slug"], "dom": c["domain"], "src": src,
            "hc": c.get("employees") or None,
        }
        try:
            return fns[c["ats"]](c["slug"], base)
        except Exception as e:
            log.warning("%s %s failed: %s", c["ats"], c["slug"], str(e)[:120])
            return []

    raws: list[dict] = []
    with ThreadPoolExecutor(workers) as ex:
        for batch in ex.map(one, targets):
            raws.extend(batch)
    data = normalize.build(raws, datetime.now(timezone.utc))
    for job in data["jobs"]:
        job["track"] = track
    normalize.write(data, out)
    log.info("wrote %d %s jobs from %d companies -> %s", len(data["jobs"]), track, len(data["companies"]), out)
    return data


def _merge(path: str, fresh: dict) -> int:
    """Add Workday jobs to an existing track file. Greenhouse rows stay put."""
    if not fresh.get("jobs"):
        return 0
    if not os.path.exists(path):
        normalize.write(fresh, path)
        return len(fresh["jobs"])
    with open(path, encoding="utf-8") as f:
        old = json.load(f)
    companies = old.get("companies") or {}
    for key, company in fresh["companies"].items():
        prev = companies.get(key)
        if not prev:
            companies[key] = company
            continue
        for field, val in company.items():
            if val and not prev.get(field):
                prev[field] = val
    jobs = list(old.get("jobs") or [])

    def title_key(job: dict) -> tuple:
        return (job.get("c"), re.sub(r"[^a-z0-9]+", " ", (job.get("t") or "").lower()).strip())

    seen = {title_key(j) for j in jobs}
    added = 0
    for job in fresh["jobs"]:
        key = title_key(job)
        if key in seen:
            continue
        seen.add(key)
        jobs.append(job)
        added += 1
    jobs.sort(key=lambda j: j.get("at") or "", reverse=True)
    normalize.write({"generated_at": fresh["generated_at"], "companies": companies, "jobs": jobs}, path)
    return added


def _drop_domains(path: str, domains: set[str]) -> None:
    """Remove one company's rows so a corrected location can be merged back."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    doc["jobs"] = [job for job in doc.get("jobs") or [] if job.get("c") not in domains]
    companies = doc.get("companies") or {}
    for domain in domains:
        companies.pop(domain, None)
    doc["companies"] = companies
    normalize.write(doc, path)


def _workday_pages(company: dict) -> list[str]:
    domain = company.get("domain") or ""
    pages = [company.get("careers"), company.get("website")]
    if domain:
        pages += [f"https://jobs.{domain}/careers", f"https://jobs.{domain}", f"https://careers.{domain}"]
    out = []
    for url in pages:
        if url and url not in out:
            out.append(url)
    return out


def crawl_known(out: str = "web/data/tech.json") -> int:
    """Microsoft, Intel, Cisco, and Salesforce. Intel and Cisco are Workday; the other two publish a list."""
    from .sources.custom import fetch_microsoft, fetch_salesforce
    from .sources.workday import fetch as workday_fetch

    specs = (
        ("intel", "intel.wd1.myworkdayjobs.com", "External", {"co": "Intel", "cslug": "External", "dom": "intel.com", "src": "tech"}),
        ("cisco", "cisco.wd5.myworkdayjobs.com", "Cisco_Careers", {"co": "Cisco", "cslug": "Cisco_Careers", "dom": "cisco.com", "src": "tech"}),
    )

    def wd(item):
        name, host, site, base = item
        try:
            return workday_fetch(host, site, base)
        except Exception as e:
            log.warning("%s workday failed: %s", name, str(e)[:160])
            return []

    raws: list[dict] = []
    with ThreadPoolExecutor(2) as ex:
        for batch in ex.map(wd, specs):
            raws.extend(batch)
    for name, fn in (("microsoft", fetch_microsoft), ("salesforce", fetch_salesforce)):
        try:
            found = fn()
        except Exception as e:
            log.warning("%s failed: %s", name, str(e)[:160])
            found = []
        log.info("%s kept %d", name, len(found))
        raws.extend(found)
    fresh = normalize.build(raws, datetime.now(timezone.utc))
    for job in fresh["jobs"]:
        job["track"] = "tech"
    added = _merge(out, fresh)
    log.info("known added %d tech jobs (%d kept after filters)", added, len(fresh["jobs"]))
    return added


def crawl_fronts(out: str = "web/data/tech.json") -> int:
    """Career pages the user named. Workday, Oracle, and Greenhouse use the existing readers."""
    from .sources.ats import greenhouse
    from .sources.custom import fetch_anduril, fetch_intuit, fetch_paypal, fetch_supermicro, fetch_synopsys
    from .sources.oracle import fetch as oracle_fetch
    from .sources.workday import fetch as workday_fetch

    workday_specs = (
        ("adobe", "adobe.wd5.myworkdayjobs.com", "external_experienced", {"co": "Adobe Inc.", "cslug": "external_experienced", "dom": "adobe.com", "src": "tech"}),
        ("broadcom", "broadcom.wd1.myworkdayjobs.com", "External_Career", {"co": "Broadcom", "cslug": "External_Career", "dom": "broadcom.com", "src": "tech"}),
        ("cadence", "cadence.wd1.myworkdayjobs.com", "External_Careers", {"co": "Cadence Design Systems", "cslug": "External_Careers", "dom": "cadence.com", "src": "tech"}),
        ("paloalto", "paloaltonetworks.wd5.myworkdayjobs.com", "panwexternalcareers", {"co": "Palo Alto Networks", "cslug": "panwexternalcareers", "dom": "paloaltonetworks.com", "src": "tech"}),
    )
    greenhouse_specs = (
        ("datadog", "datadog", {"co": "Datadog", "cslug": "datadog", "dom": "datadoghq.com", "src": "tech"}),
        ("taketwo", "taketwo", {"co": "Take-Two Interactive", "cslug": "taketwo", "dom": "take2games.com", "src": "tech"}),
    )

    def wd(item):
        name, host, site, base = item
        try:
            return workday_fetch(host, site, base)
        except Exception as e:
            log.warning("%s workday failed: %s", name, str(e)[:160])
            return []

    raws: list[dict] = []
    with ThreadPoolExecutor(2) as ex:
        for batch in ex.map(wd, workday_specs):
            raws.extend(batch)
    try:
        raws.extend(oracle_fetch("enterpriseplatform.dell.com", {"co": "Dell Technologies", "cslug": "dell", "dom": "delltechnologies.com", "src": "tech"}))
    except Exception as e:
        log.warning("dell oracle failed: %s", str(e)[:160])
    for name, slug, base in greenhouse_specs:
        try:
            found = greenhouse(slug, base, timeout=120)
        except Exception as e:
            log.warning("%s greenhouse failed: %s", name, str(e)[:160])
            found = []
        log.info("%s greenhouse kept raw %d", name, len(found))
        raws.extend(found)
    for name, fn in (
        ("paypal", fetch_paypal),
        ("intuit", fetch_intuit),
        ("synopsys", fetch_synopsys),
        ("supermicro", fetch_supermicro),
        ("anduril", fetch_anduril),
    ):
        try:
            found = fn()
        except Exception as e:
            log.warning("%s failed: %s", name, str(e)[:160])
            found = []
        log.info("%s kept %d", name, len(found))
        raws.extend(found)
    fresh = normalize.build(raws, datetime.now(timezone.utc))
    for job in fresh["jobs"]:
        job["track"] = "tech"
    added = _merge(out, fresh)
    log.info("fronts added %d tech jobs (%d kept after filters)", added, len(fresh["jobs"]))
    return added


def _stamp(domain: str, fields: dict) -> None:
    doc = load_registry("data/tech_companies.json")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for company in doc["companies"]:
        if company.get("domain") != domain:
            continue
        company.update({**fields, "error": None, "checked": now})
    save_registry("data/tech_companies.json", doc)


def _ensure_cloudflare() -> None:
    doc = load_registry("data/tech_companies.json")
    if any(c.get("domain") == "cloudflare.com" for c in doc["companies"]):
        return
    row = {
        "name": "Cloudflare",
        "ticker": "NET",
        "kind": "public",
        "sector": "Information Technology",
        "hq": "San Francisco, California",
        "website": "https://www.cloudflare.com",
        "domain": "cloudflare.com",
        "ats": None,
        "slug": None,
        "careers": None,
        "error": None,
        "checked": None,
    }
    at = next((i for i, c in enumerate(doc["companies"]) if c.get("domain") == "cognizant.com"), len(doc["companies"]))
    doc["companies"].insert(at, row)
    save_registry("data/tech_companies.json", doc)


def _ensure_pinterest() -> None:
    doc = load_registry("data/tech_companies.json")
    if any(c.get("domain") == "pinterest.com" for c in doc["companies"]):
        return
    row = {
        "name": "Pinterest",
        "ticker": "PINS",
        "kind": "public",
        "sector": "Communication Services",
        "hq": "San Francisco, California",
        "website": "https://pinterest.com",
        "domain": "pinterest.com",
        "ats": None,
        "slug": None,
        "careers": None,
        "error": None,
        "checked": None,
    }
    at = next((i for i, c in enumerate(doc["companies"]) if c.get("domain") == "paypal.com"), len(doc["companies"]))
    doc["companies"].insert(at, row)
    save_registry("data/tech_companies.json", doc)


def _ensure_roblox() -> None:
    doc = load_registry("data/tech_companies.json")
    if any(c.get("domain") == "roblox.com" for c in doc["companies"]):
        return
    row = {
        "name": "Roblox",
        "ticker": "RBLX",
        "kind": "public",
        "sector": "Communication Services",
        "hq": "San Mateo, California",
        "website": "https://roblox.com",
        "domain": "roblox.com",
        "ats": None,
        "slug": None,
        "careers": None,
        "error": None,
        "checked": None,
    }
    at = next((i for i, c in enumerate(doc["companies"]) if c.get("domain") == "roperind.com"), len(doc["companies"]))
    doc["companies"].insert(at, row)
    save_registry("data/tech_companies.json", doc)


def crawl_pages(out: str = "web/data/tech.json") -> int:
    """Greenhouse boards named by Cloudflare, DoorDash, Pinterest, and Roblox career pages."""
    from .sources.ats import greenhouse, greenhouse_posting_places

    _ensure_cloudflare()
    _ensure_pinterest()
    _ensure_roblox()
    specs = (
        ("cloudflare.com", lambda: greenhouse("cloudflare", {"co": "Cloudflare", "cslug": "cloudflare", "dom": "cloudflare.com", "src": "tech"}, places=greenhouse_posting_places),
         {"ats": "greenhouse", "slug": "cloudflare", "careers": "https://www.cloudflare.com/careers/jobs/"}),
        ("doordash.com", lambda: greenhouse("doordashusa", {"co": "DoorDash", "cslug": "doordash", "dom": "doordash.com", "src": "tech"}),
         {"ats": "greenhouse", "slug": "doordashusa", "careers": "https://careersatdoordash.com/job-search/"}),
        ("pinterest.com", lambda: greenhouse("pinterest", {"co": "Pinterest", "cslug": "pinterest", "dom": "pinterest.com", "src": "tech"}),
         {"ats": "greenhouse", "slug": "pinterest", "careers": "https://www.pinterestcareers.com/jobs/"}),
        ("roblox.com", lambda: greenhouse("roblox", {"co": "Roblox", "cslug": "roblox", "dom": "roblox.com", "src": "tech"}),
         {"ats": "greenhouse", "slug": "roblox", "careers": "https://careers.roblox.com/jobs"}),
    )
    raws: list[dict] = []
    for domain, pull, fields in specs:
        try:
            found = pull()
        except Exception as e:
            log.warning("%s failed: %s", domain, str(e)[:160])
            found = []
        log.info("%s kept %d", domain, len(found))
        if found:
            _stamp(domain, fields)
        raws.extend(found)
    fresh = normalize.build(raws, datetime.now(timezone.utc))
    for job in fresh["jobs"]:
        job["track"] = "tech"
    if any(job.get("c") == "cloudflare.com" for job in fresh["jobs"]):
        _drop_domains(out, {"cloudflare.com"})
    added = _merge(out, fresh)
    log.info("pages added %d tech jobs (%d kept after filters)", added, len(fresh["jobs"]))
    return added


def crawl_named(out: str = "web/data/tech.json") -> int:
    """Veeva, FICO, Motorola Solutions, and Western Digital. Each board is one the public page already names."""
    from .sources.ats import lever, smartrecruiters
    from .sources.workday import fetch as workday_fetch

    specs = (
        ("veeva.com", lambda: lever("veeva", {"co": "Veeva Systems", "cslug": "veeva", "dom": "veeva.com", "src": "tech"}),
         {"ats": "lever", "slug": "veeva", "careers": "https://jobs.lever.co/veeva"}),
        ("fico.com", lambda: workday_fetch("fico.wd1.myworkdayjobs.com", "External", {"co": "Fair Isaac", "cslug": "fico", "dom": "fico.com", "src": "tech"}),
         {"ats": "workday", "slug": "fico.wd1.myworkdayjobs.com", "site": "External", "sites": ["External"], "careers": "https://fico.wd1.myworkdayjobs.com/External"}),
        ("motorolasolutions.com", lambda: workday_fetch("motorolasolutions.wd5.myworkdayjobs.com", "Careers", {"co": "Motorola Solutions", "cslug": "motorola", "dom": "motorolasolutions.com", "src": "tech"}),
         {"ats": "workday", "slug": "motorolasolutions.wd5.myworkdayjobs.com", "site": "Careers", "sites": ["Careers"], "careers": "https://motorolasolutions.wd5.myworkdayjobs.com/Careers"}),
        ("westerndigital.com", lambda: smartrecruiters("WesternDigital", {"co": "Western Digital", "cslug": "westerndigital", "dom": "westerndigital.com", "src": "tech"}),
         {"ats": "smartrecruiters", "slug": "WesternDigital", "careers": "https://www.westerndigital.com/careers"}),
    )
    raws: list[dict] = []
    for domain, pull, fields in specs:
        try:
            found = pull()
        except Exception as e:
            log.warning("%s failed: %s", domain, str(e)[:160])
            found = []
        log.info("%s kept %d", domain, len(found))
        if found:
            _stamp(domain, fields)
        raws.extend(found)
    fresh = normalize.build(raws, datetime.now(timezone.utc))
    for job in fresh["jobs"]:
        job["track"] = "tech"
    added = _merge(out, fresh)
    log.info("named added %d tech jobs (%d kept after filters)", added, len(fresh["jobs"]))
    return added


def crawl_amd(out: str = "web/data/tech.json") -> int:
    """AMD's careers page loads a public job list. The old iCIMS slug was an internal board."""
    from .sources.custom import fetch_amd

    try:
        raws = fetch_amd()
    except Exception as e:
        log.warning("amd failed: %s", str(e)[:160])
        raws = []
    doc = load_registry("data/tech_companies.json")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for company in doc["companies"]:
        if company.get("domain") != "amd.com":
            continue
        company.pop("site", None)
        company.pop("sites", None)
        company.update({
            "ats": None,
            "slug": None,
            "careers": "https://careers.amd.com/careers-home/jobs",
            "error": None,
            "checked": now,
        })
    save_registry("data/tech_companies.json", doc)
    fresh = normalize.build(raws, datetime.now(timezone.utc))
    for job in fresh["jobs"]:
        job["track"] = "tech"
    added = _merge(out, fresh)
    log.info("amd added %d tech jobs (%d kept after filters)", added, len(fresh["jobs"]))
    return added


def crawl_uber(out: str = "web/data/tech.json") -> int:
    """jobs.uber.com signs in through this Oracle board. The front page itself is behind Cloudflare."""
    from .sources.oracle import fetch as oracle_fetch

    host = "iaziqy.fa.ocs.oraclecloud.com"
    try:
        raws = oracle_fetch(host, {"co": "Uber", "cslug": "CX_1", "dom": "uber.com", "src": "tech"})
    except Exception as e:
        log.warning("uber oracle failed: %s", str(e)[:160])
        raws = []
    doc = load_registry("data/tech_companies.json")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for company in doc["companies"]:
        if company.get("domain") != "uber.com":
            continue
        company.update({
            "ats": "oracle",
            "slug": host,
            "site": "CX_1",
            "sites": ["CX_1"],
            "careers": "https://jobs.uber.com/",
            "error": None,
            "checked": now,
        })
    save_registry("data/tech_companies.json", doc)
    fresh = normalize.build(raws, datetime.now(timezone.utc))
    for job in fresh["jobs"]:
        job["track"] = "tech"
    added = _merge(out, fresh)
    log.info("uber added %d tech jobs (%d kept after filters)", added, len(fresh["jobs"]))
    return added


def crawl_eightfold(out: str = "web/data/tech.json") -> int:
    """Qualcomm, Applied Materials, HP, Autodesk, Micron, and Lam use a public search. NetApp publishes HTML."""
    from .sources.custom import fetch_netapp, fetch_pcsx

    specs = (
        ("qualcomm", "https://careers.qualcomm.com", "qualcomm.com"),
        ("appliedmaterials", "https://careers.appliedmaterials.com", "appliedmaterials.com"),
        ("hp", "https://apply.hp.com", "hp.com"),
        ("autodesk", "https://jobs.autodesk.com", "autodesk.com"),
        ("micron", "https://careers.micron.com", "micron.com"),
        ("lam", "https://careers.lamresearch.com", "lamresearch.com"),
    )
    raws: list[dict] = []
    for which, origin, domain in specs:
        try:
            found = fetch_pcsx(which, origin, domain)
        except Exception as e:
            log.warning("%s failed: %s", which, str(e)[:160])
            found = []
        log.info("%s kept %d", which, len(found))
        raws.extend(found)
    try:
        found = fetch_netapp()
    except Exception as e:
        log.warning("netapp failed: %s", str(e)[:160])
        found = []
    log.info("netapp kept %d", len(found))
    raws.extend(found)
    fresh = normalize.build(raws, datetime.now(timezone.utc))
    for job in fresh["jobs"]:
        job["track"] = "tech"
    added = _merge(out, fresh)
    log.info("eightfold added %d tech jobs (%d kept after filters)", added, len(fresh["jobs"]))
    return added


def crawl_custom(out: str = "web/data/tech.json") -> int:
    """Read the six companies that publish jobs on their own site, and merge them."""
    from .sources.custom import fetch_all

    raws = fetch_all()
    fresh = normalize.build(raws, datetime.now(timezone.utc))
    for job in fresh["jobs"]:
        job["track"] = "tech"
    added = _merge(out, fresh)
    log.info("custom added %d tech jobs (%d kept after filters)", added, len(fresh["jobs"]))
    return added


def crawl_workday(path: str, out: str, src: str = "tech", track: str = "tech", workers: int = 4) -> int:
    """Find Workday site paths, pull US eng/PM roles, and merge them into the track file."""
    from .sources.workday import choose_sites, discover_sites, fetch

    doc = load_registry(path)
    targets = [c for c in doc["companies"] if c.get("ats") in (None, "workday")]
    log.info("resolving Workday sites for %d companies", len(targets))

    def resolve(company: dict):
        found = []
        for url in _workday_pages(company):
            try:
                final, text = _get(url)
            except requests.RequestException:
                continue
            found.extend(discover_sites(f"{final}\n{text}"))
            if found:
                break
        sites = choose_sites(found)
        if not sites and company.get("slug") and company.get("site"):
            sites = [(company["slug"], s) for s in (company.get("sites") or [company["site"]])]
        return company, sites

    resolved = []
    with ThreadPoolExecutor(12) as ex:
        for company, sites in ex.map(resolve, targets):
            if not sites:
                continue
            company["ats"] = "workday"
            company["slug"] = sites[0][0]
            company["site"] = sites[0][1]
            company["sites"] = [site for _host, site in sites]
            resolved.append((company, sites))
    save_registry(path, doc)
    log.info("crawling %d Workday boards", len(resolved))

    def one(item):
        company, sites = item
        base = {
            "co": company["name"], "cslug": sites[0][1], "dom": company.get("domain"),
            "src": src, "hc": company.get("employees") or None,
        }
        raws = []
        for host, site in sites:
            try:
                raws.extend(fetch(host, site, base))
            except Exception as e:
                log.warning("workday %s/%s failed: %s", host, site, str(e)[:120])
        return raws

    raws = []
    with ThreadPoolExecutor(workers) as ex:
        for batch in ex.map(one, resolved):
            raws.extend(batch)
    fresh = normalize.build(raws, datetime.now(timezone.utc))
    for job in fresh["jobs"]:
        job["track"] = track
    added = _merge(out, fresh)
    log.info("workday added %d %s jobs (%d kept after filters) -> %s", added, track, len(fresh["jobs"]), out)
    return added


def _job_domains(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return {job.get("c") for job in doc.get("jobs") or [] if job.get("c")}


def _company_base(company: dict, src: str) -> dict:
    return {
        "co": company["name"],
        "cslug": company.get("slug") or company.get("domain"),
        "dom": company.get("domain"),
        "src": src,
        "hc": company.get("employees") or None,
    }


def reprobe_undetected(path: str) -> int:
    """Look at careers. and jobs. hostnames for companies whose site named no board."""
    from .sources.careers import classify_board
    from .sources.workday import choose_sites, discover_sites

    doc = load_registry(path)
    pending = [c for c in doc["companies"] if not c.get("ats")]
    log.info("rechecking career sites for %d companies", len(pending))
    found = 0
    lock = threading.Lock()

    def one(company: dict) -> dict | None:
        domain = (company.get("domain") or "").lower().removeprefix("www.")
        for url in _fallbacks(company.get("website") or f"https://{domain}"):
            try:
                final, text = _get(url)
            except requests.RequestException:
                continue
            blob = f"{final}\n{text}"
            hit = classify_board(final, text) or {}
            pairs = choose_sites(discover_sites(blob))
            if hit.get("ats") == "workday" and pairs:
                hit["slug"] = pairs[0][0]
                hit["site"] = pairs[0][1]
                hit["sites"] = [site for _host, site in pairs]
            elif not hit and pairs:
                hit = {
                    "ats": "workday",
                    "slug": pairs[0][0],
                    "site": pairs[0][1],
                    "sites": [site for _host, site in pairs],
                }
            if hit.get("ats"):
                hit["careers"] = final
                hit["checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                return hit
        return None

    def apply(company: dict) -> None:
        nonlocal found
        hit = one(company)
        if not hit:
            return
        with lock:
            company.update(hit)
            found += 1
            log.info("found %s for %s", hit["ats"], company.get("name"))

    if pending:
        with ThreadPoolExecutor(12) as ex:
            list(ex.map(apply, pending))
        save_registry(path, doc)
    log.info("career sites named a board for %d companies", found)
    return found


def _crawl_missing(path: str, out: str, src: str, track: str) -> int:
    """Pull boards for companies that are still absent from the track file."""
    from .sources import ats
    from .sources.oracle import fetch as oracle_fetch
    from .sources.workday import choose_sites, discover_sites, fetch as workday_fetch

    doc = load_registry(path)
    have = _job_domains(out)
    missing = [
        c for c in doc["companies"]
        if (c.get("domain") or "").lower().removeprefix("www.") not in have
    ]
    raws: list[dict] = []
    fns = {"greenhouse": ats.greenhouse, "lever": ats.lever, "ashby": ats.ashby}

    def board(company: dict) -> list[dict]:
        try:
            return fns[company["ats"]](company["slug"], _company_base(company, src))
        except Exception as e:
            log.warning("%s %s failed: %s", company.get("ats"), company.get("slug"), str(e)[:120])
            return []

    boards = [c for c in missing if c.get("ats") in CRAWLABLE and c.get("slug")]
    if boards:
        with ThreadPoolExecutor(8) as ex:
            for batch in ex.map(board, boards):
                raws.extend(batch)

    def ora(company: dict) -> list[dict]:
        try:
            return oracle_fetch(company["slug"], _company_base(company, src))
        except Exception as e:
            log.warning("oracle %s failed: %s", company.get("slug"), str(e)[:120])
            return []

    oracles = [c for c in missing if c.get("ats") == "oracle" and c.get("slug")]
    log.info("reading %d Oracle boards", len(oracles))
    if oracles:
        with ThreadPoolExecutor(4) as ex:
            for batch in ex.map(ora, oracles):
                raws.extend(batch)

    for company in missing:
        if company.get("ats") != "smartrecruiters" or not company.get("slug"):
            continue
        try:
            raws.extend(ats.smartrecruiters(company["slug"], _company_base(company, src)))
        except Exception as e:
            log.warning("smartrecruiters %s failed: %s", company.get("slug"), str(e)[:120])

    def wd(company: dict) -> list[dict]:
        if company.get("slug") and company.get("site"):
            sites = [(company["slug"], site) for site in (company.get("sites") or [company["site"]])]
        else:
            found = []
            for url in _workday_pages(company):
                try:
                    final, text = _get(url)
                except requests.RequestException:
                    continue
                found.extend(discover_sites(f"{final}\n{text}"))
                if found:
                    break
            sites = choose_sites(found)
        base = _company_base(company, src)
        rows = []
        for host, site in sites:
            try:
                rows.extend(workday_fetch(host, site, base))
            except Exception as e:
                log.warning("workday %s/%s failed: %s", host, site, str(e)[:120])
        return rows

    workdays = [c for c in missing if c.get("ats") == "workday"]
    if workdays:
        with ThreadPoolExecutor(3) as ex:
            for batch in ex.map(wd, workdays):
                raws.extend(batch)

    fresh = normalize.build(raws, datetime.now(timezone.utc))
    for job in fresh["jobs"]:
        job["track"] = track
    added = _merge(out, fresh)
    log.info("gaps added %d %s jobs (%d kept after filters)", added, track, len(fresh["jobs"]))
    return added


def crawl_gaps() -> int:
    """Fill companies that are on the list and still have no jobs."""
    added = 0
    for path, out, src, track in (
        ("data/tech_companies.json", "web/data/tech.json", "tech", "tech"),
        ("data/traditional_companies.json", "web/data/traditional.json", "traditional", "traditional"),
    ):
        reprobe_undetected(path)
        added += _crawl_missing(path, out, src, track)
    return added
