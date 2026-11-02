"""Crawl a16z + YC startups and write web/data/jobs.json.

    python -m crawler
    python -m crawler --from-snapshots snapshots/
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from . import monitor, normalize, tech
from .sources import ats, discovery

log = logging.getLogger("crawler")


def public_company_domains(path: str = "data/tech_companies.json") -> set[str]:
    """Domains of companies already on the public-tech list."""
    doc = tech.load_registry(path)
    return {
        (c.get("domain") or "").lower().removeprefix("www.")
        for c in doc["companies"]
        if c.get("kind") == "public" and c.get("domain")
    }


def crawl_live(workers: int = 8) -> list[dict]:
    s = requests.Session()
    raws: list[dict] = []
    public = public_company_domains()

    # a16z portfolio -> ATS boards
    cos = discovery.without_public(
        [c for c in discovery.a16z_companies(s) if c["jobCount"] > 0 and c["stage"] in discovery.STARTUP_STAGES],
        public,
    )
    log.info("a16z: %d hiring startups", len(cos))
    with ThreadPoolExecutor(workers) as ex:
        boards = dict(zip((c["slug"] for c in cos), ex.map(lambda c: _safe(discovery.a16z_board, s, c["slug"]), cos)))

    def a16z_jobs(c: dict) -> list[dict]:
        board = boards.get(c["slug"])
        if not board:
            return []
        host, slug = board.split("/", 1)
        fn = ats.ADAPTERS.get(host)
        base = {"co": c["name"], "cslug": c["slug"], "dom": c["domain"], "stage": c["stage"], "hc": c["headcount"],
                "mk": c["markets"], "logo": c["logo"], "src": "a16z"}
        return _safe(fn, slug, base) or [] if fn else []

    with ThreadPoolExecutor(workers) as ex:
        for batch in ex.map(a16z_jobs, cos):
            raws.extend(batch)
    log.info("a16z: %d raw jobs", len(raws))

    # YC hiring companies. Listed companies stay off this track.
    yc = discovery.without_public(discovery.yc_hiring_companies(s), public)
    log.info("YC: %d hiring companies", len(yc))
    n0 = len(raws)
    with ThreadPoolExecutor(max(2, workers // 2)) as ex:
        for batch in ex.map(lambda c: _safe(discovery.yc_jobs, s, c) or [], yc):
            raws.extend(batch)
    log.info("YC: %d raw jobs", len(raws) - n0)
    return raws


def load_snapshots(folder: str) -> tuple[list[dict], datetime]:
    raws, stamps = [], []
    for path in sorted(glob.glob(f"{folder.rstrip('/')}/*.json")):
        d = json.load(open(path, encoding="utf-8"))
        raws.extend(d["jobs"])
        stamps.append(datetime.fromisoformat(d["crawled_at"].replace("Z", "+00:00")))
    return raws, max(stamps)


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # one broken board must never stop the crawl
        log.warning("%s%s failed: %s", fn.__name__, args[1:2] if len(args) > 1 else args[:1], str(e)[:120])
        return None


def enrich_profiles(path: str) -> int:
    """Fill bio / founding year / CEO on YC companies already in jobs.json.

    Slugs come from YC apply links in the file, then from the public yc-oss company list
    matched by domain. a16z-only companies have no such page, so they stay as they are.
    """
    import requests

    from .sources.ats import TIMEOUT, UA
    from .sources.discovery import company_from_page, profile_from_yc

    data = json.load(open(path, encoding="utf-8"))
    companies: dict = data["companies"]
    slugs: dict[str, str] = {}
    for j in data["jobs"]:
        m = re.search(r"ycombinator\.com/companies/([^/]+)/", j.get("url") or "")
        if m:
            slugs.setdefault(j["c"], m.group(1))

    listed = requests.get("https://yc-oss.github.io/api/companies/all.json", headers=UA, timeout=60).json()
    by_dom = {}
    for c in listed:
        dom = re.sub(r"^https?://(?:www\.)?", "", (c.get("website") or "")).split("/")[0].lower()
        if dom:
            by_dom[dom] = c["slug"]
    for key, c in companies.items():
        if key in slugs or "YC" not in (c.get("backers") or []):
            continue
        dom = (c.get("d") or "").lower().removeprefix("www.")
        if dom in by_dom:
            slugs[key] = by_dom[dom]

    wanted = [(key, slug) for key, slug in slugs.items() if key in companies]
    log.info("fetching profiles for %d YC companies", len(wanted))

    def fetch(item: tuple[str, str]) -> tuple[str, dict]:
        key, slug = item
        try:
            page = requests.get(f"https://www.ycombinator.com/companies/{slug}", headers=UA, timeout=TIMEOUT).text
            return key, profile_from_yc(company_from_page(page))
        except Exception as e:
            log.warning("profile %s failed: %s", slug, str(e)[:120])
            return key, {}

    filled = 0
    with ThreadPoolExecutor(8) as ex:
        for i, (key, prof) in enumerate(ex.map(fetch, wanted), 1):
            if not prof:
                continue
            companies[key].update(prof)
            filled += 1
            if i % 100 == 0:
                log.info("profiles %d/%d", i, len(wanted))
    normalize.write(data, path)
    log.info("profiles filled %d/%d -> %s", filled, len(wanted), path)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="crawler")
    ap.add_argument("--from-snapshots", help="folder of browser snapshot JSON files")
    ap.add_argument("--enrich", action="store_true", help="add YC company bios to an existing jobs.json")
    ap.add_argument("--tech-list", help="Wikidata export JSON; writes the tech company registry")
    ap.add_argument("--tech", action="store_true", help="detect ATS from official sites and crawl Greenhouse, Lever, Ashby")
    ap.add_argument("--traditional", action="store_true", help="detect ATS for recognizable US traditional companies and crawl Greenhouse, Lever, Ashby")
    ap.add_argument("--monitor", nargs="*", metavar="FILE", help="recheck saved jobs against the board API, not HTTP 200")
    ap.add_argument("--refresh", choices=["tech", "traditional", "all"], help="rebuild Tech and Traditional from the current readers; a failed company keeps yesterday's rows")
    ap.add_argument("--workday", action="store_true", help="pull Workday boards and merge them into the tech and traditional tracks")
    ap.add_argument("--house", action="store_true", help="pull the traditional boards whose career pages hide the ATS")
    ap.add_argument("--custom", action="store_true", help="pull Google, Amazon, Apple, Netflix, Meta, Tesla, Microsoft, and Salesforce")
    ap.add_argument("--known", action="store_true", help="pull Microsoft, Intel, Cisco, and Salesforce")
    ap.add_argument("--fronts", action="store_true", help="pull Adobe, Anduril, Broadcom, Cadence, Palo Alto Networks, Dell, Datadog, Take-Two, PayPal, Intuit, Synopsys, and Supermicro")
    ap.add_argument("--eightfold", action="store_true", help="pull Qualcomm, Applied Materials, HP, Autodesk, Micron, Lam Research, and NetApp")
    ap.add_argument("--open", action="store_true", help="pull the boards whose public search already matches an existing reader")
    ap.add_argument("--amd", action="store_true", help="pull AMD from careers.amd.com")
    ap.add_argument("--named", action="store_true", help="pull Veeva, FICO, Motorola Solutions, and Western Digital")
    ap.add_argument("--pages", action="store_true", help="pull Cloudflare and DoorDash from the Greenhouse boards their career pages use")
    ap.add_argument("--gaps", action="store_true", help="recheck companies with no jobs and pull Oracle, SmartRecruiters, and any board the career site names")
    ap.add_argument("--registry", default="data/tech_companies.json")
    ap.add_argument("--out", default="web/data/jobs.json")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if a.tech_list:
        tech.write_list(a.tech_list, a.registry)
        return 0
    if a.known:
        tech.crawl_known()
        return 0
    if a.fronts:
        tech.crawl_fronts()
        return 0
    if a.eightfold:
        tech.crawl_eightfold()
        return 0
    if a.open:
        from .house import crawl_open
        crawl_open()
        return 0
    if a.amd:
        tech.crawl_amd()
        return 0
    if a.named:
        tech.crawl_named()
        return 0
    if a.pages:
        tech.crawl_pages()
        return 0
    if a.custom:
        tech.crawl_custom()
        return 0
    if a.gaps:
        tech.crawl_gaps()
        return 0
    if a.house:
        from .house import crawl_house
        crawl_house()
        return 0
    if a.workday:
        tech.crawl_workday("data/tech_companies.json", "web/data/tech.json", src="tech", track="tech")
        tech.crawl_workday("data/traditional_companies.json", "web/data/traditional.json", src="traditional", track="traditional")
        return 0
    if a.refresh:
        from .refresh import refresh_tracks
        return refresh_tracks(a.refresh)
    if a.monitor is not None:
        known = monitor.slug_map(["data/tech_companies.json", "data/traditional_companies.json"])
        paths = a.monitor or ["web/data/jobs.json", "web/data/tech.json", "web/data/traditional.json"]
        for path in paths:
            monitor.check_file(path, known)
        return 0
    if a.traditional:
        reg = "data/traditional_companies.json"
        tech.detect_all(reg)
        tech.crawl_boards(reg, "web/data/traditional.json", src="traditional", track="traditional")
        return 0
    if a.tech:
        tech.detect_all(a.registry)
        tech.crawl_boards(a.registry, "web/data/tech.json")
        return 0
    if a.enrich:
        return enrich_profiles(a.out)
    if a.from_snapshots:
        raws, ts = load_snapshots(a.from_snapshots)
    else:
        raws, ts = crawl_live(), datetime.now(timezone.utc)
    data = normalize.build(raws, ts)
    previous = []
    if os.path.exists(a.out):
        try:
            with open(a.out, encoding="utf-8") as f:
                previous = json.load(f).get("jobs") or []
        except (OSError, json.JSONDecodeError):
            previous = []
    normalize.note_changes(previous, data["jobs"])
    normalize.write(data, a.out)
    log.info("wrote %d jobs from %d companies -> %s", len(data["jobs"]), len(data["companies"]), a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
