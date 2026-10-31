"""Crawl a16z + YC startups and write web/data/jobs.json.

    python -m crawler.run                       # full live crawl
    python -m crawler.run --from-snapshots snapshots/   # rebuild from browser snapshots
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from . import normalize
from .sources import ats, discovery

log = logging.getLogger("crawler")


def crawl_live(workers: int = 8) -> list[dict]:
    s = requests.Session()
    raws: list[dict] = []

    # a16z portfolio -> ATS boards
    cos = [c for c in discovery.a16z_companies(s) if c["jobCount"] > 0 and c["stage"] in discovery.STARTUP_STAGES]
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

    # YC hiring companies
    yc = discovery.yc_hiring_companies(s)
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
        d = json.load(open(path))
        raws.extend(d["jobs"])
        stamps.append(datetime.fromisoformat(d["crawled_at"].replace("Z", "+00:00")))
    return raws, max(stamps)


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # one broken board must never stop the crawl
        log.warning("%s%s failed: %s", fn.__name__, args[1:2] if len(args) > 1 else args[:1], str(e)[:120])
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-snapshots", help="folder of browser snapshot JSON files")
    ap.add_argument("--out", default="web/data/jobs.json")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if a.from_snapshots:
        raws, ts = load_snapshots(a.from_snapshots)
    else:
        raws, ts = crawl_live(), datetime.now(timezone.utc)
    data = normalize.build(raws, ts)
    normalize.write(data, a.out)
    log.info("wrote %d jobs from %d companies -> %s", len(data["jobs"]), len(data["companies"]), a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
