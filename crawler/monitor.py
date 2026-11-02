"""Check whether a saved job is still the same posting.

An HTTP 200 is not enough. Career sites return 200 for a closed role, and they
return 200 again after the description has been rewritten. Greenhouse, Lever,
and Ashby are checked against the board API: a role is open only when that API
still lists its id, and the description is the same only when the body hash
matches. YC roles are checked against the company jobs index, then the job
page's structured description, not the status code.
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from . import normalize
from .sources.ats import TIMEOUT, UA, strip_html

log = logging.getLogger("crawler")

_GH_PATH = re.compile(r"greenhouse\.io/([^/?#]+)/jobs/(\d+)", re.I)
_GH_JID = re.compile(r"[?&]gh_jid=(\d+)")
_LEVER = re.compile(r"jobs\.lever\.co/([^/?#]+)/([0-9a-f-]{8,})", re.I)
_ASHBY = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{8,})", re.I)
_YC = re.compile(r"ycombinator\.com/companies/([^/?#]+)/jobs/([^/?#]+)", re.I)
_PAGE = re.compile(r'data-page="([^"]+)"')
_CLOSED = re.compile(
    r"the job you are looking for is no longer open|"
    r"this (?:job|position|role) (?:is no longer available|has been closed|has been filled|has expired)|"
    r"this posting has expired|no longer accepting applications",
    re.I,
)


def parse_posting(url: str) -> dict | None:
    """Provider, board slug (when the URL has one), and the posting id."""
    if not url:
        return None
    m = _GH_PATH.search(url)
    if m and m.group(1).lower() != "embed":
        return {"p": "greenhouse", "slug": m.group(1).lower(), "jid": m.group(2)}
    m = _GH_JID.search(url)
    if m:
        return {"p": "greenhouse", "slug": None, "jid": m.group(1)}
    m = _LEVER.search(url)
    if m:
        return {"p": "lever", "slug": m.group(1).lower(), "jid": m.group(2).lower()}
    m = _ASHBY.search(url)
    if m:
        return {"p": "ashby", "slug": m.group(1).lower(), "jid": m.group(2).lower()}
    m = _YC.search(url)
    if m:
        return {"p": "ycombinator", "slug": m.group(1), "jid": m.group(2)}
    return None


def page_signal(status: int, body: str, job_id: str) -> str:
    """Read a fetched page. Status 200 by itself is unknown, not open."""
    if status in (404, 410):
        return "closed"
    if status != 200:
        return "unknown"
    text = body or ""
    if _CLOSED.search(text):
        return "closed"
    if job_id and job_id not in text:
        return "closed"
    return "unknown"


def apply_check(job: dict, *, board_ok: bool, present: bool, live_hash: str | None) -> str:
    """Stamp one job. A failed board read never marks the role closed."""
    if not board_ok:
        return "unknown"
    if not present:
        job["closed"] = True
        job.pop("chg", None)
        return "closed"
    job.pop("closed", None)
    if live_hash and job.get("dh") and live_hash != job["dh"]:
        job["chg"] = True
        return "changed"
    if live_hash and not job.get("dh"):
        job["dh"] = live_hash
    elif job.get("chg"):
        return "changed"
    return "open"


def _get(url: str, **params) -> requests.Response:
    return requests.get(url, params=params or None, headers=UA, timeout=TIMEOUT)


def fetch_board(provider: str, slug: str) -> dict | None:
    """Open postings as id -> description. None when the board cannot be read."""
    try:
        if provider == "greenhouse":
            r = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", content="true")
            if r.status_code != 200:
                return None
            return {str(j["id"]): strip_html(j.get("content")) for j in r.json().get("jobs") or []}
        if provider == "lever":
            r = _get(f"https://api.lever.co/v0/postings/{slug}", mode="json")
            if r.status_code != 200 or not isinstance(r.json(), list):
                return None
            out = {}
            for j in r.json():
                body = (j.get("descriptionPlain") or "") + " " + " ".join(
                    strip_html(item.get("content")) for item in j.get("lists") or []
                )
                out[str(j["id"]).lower()] = body
            return out
        if provider == "ashby":
            r = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
            if r.status_code != 200:
                return None
            return {str(j["id"]).lower(): j.get("descriptionPlain") or "" for j in r.json().get("jobs") or []}
    except (requests.RequestException, ValueError, KeyError):
        return None
    return None


def fetch_yc_index(slug: str) -> list | None:
    try:
        r = _get(f"https://www.ycombinator.com/companies/{slug}/jobs")
        if r.status_code != 200:
            return None
        m = _PAGE.search(r.text)
        if not m:
            return None
        props = json.loads(html_lib.unescape(m.group(1))).get("props") or {}
        return props.get("jobPostings") or []
    except (requests.RequestException, ValueError):
        return None


def yc_listed(postings: list, tail: str) -> bool:
    for j in postings:
        url = (j.get("url") or "").rstrip("/")
        jid = str(j.get("id") or "")
        if url.endswith("/" + tail) or url.endswith(tail):
            return True
        if jid and (tail == jid or tail.startswith(jid + "-")):
            return True
    return False


def fetch_yc_description(url: str, tail: str) -> tuple[str, str | None]:
    """(open|closed|unknown, description). A 200 page without that job is not open."""
    try:
        r = _get(url)
    except requests.RequestException:
        return "unknown", None
    signal = page_signal(r.status_code, r.text, tail)
    if signal == "closed":
        return "closed", None
    m = _PAGE.search(r.text or "")
    if r.status_code != 200 or not m:
        return "unknown", None
    try:
        job = (json.loads(html_lib.unescape(m.group(1))).get("props") or {}).get("job") or {}
    except ValueError:
        return "unknown", None
    if not yc_listed([job], tail):
        return "unknown", None
    return "open", job.get("description") or ""


def slug_map(paths: list[str]) -> dict[str, tuple[str, str]]:
    out = {}
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except OSError:
            continue
        for c in doc.get("companies") or []:
            if c.get("ats") in ("greenhouse", "lever", "ashby") and c.get("slug") and c.get("domain"):
                out[c["domain"]] = (c["ats"], c["slug"].lower())
    return out


def _locate(job: dict, boards_by_domain: dict[str, tuple[str, str]]) -> dict | None:
    parsed = parse_posting(job.get("url") or "")
    if parsed and parsed["slug"]:
        return parsed
    known = boards_by_domain.get(job.get("c") or "")
    if parsed and parsed["p"] == "greenhouse" and known and known[0] == "greenhouse":
        return {**parsed, "slug": known[1]}
    if not parsed and known:
        return None
    return parsed


def check_file(path: str, boards_by_domain: dict[str, tuple[str, str]], workers: int = 8) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    jobs = data.get("jobs") or []
    located = [(j, _locate(j, boards_by_domain)) for j in jobs]

    board_keys = {(p["p"], p["slug"]) for _, p in located if p and p["slug"] and p["p"] in ("greenhouse", "lever", "ashby")}
    yc_slugs = {p["slug"] for _, p in located if p and p["p"] == "ycombinator"}
    log.info("%s: %d jobs, %d boards, %d yc companies", path, len(jobs), len(board_keys), len(yc_slugs))

    boards: dict[tuple[str, str], dict | None] = {}
    with ThreadPoolExecutor(workers) as ex:
        fetched = ex.map(lambda k: (k, fetch_board(*k)), board_keys)
        for key, body in fetched:
            boards[key] = body
        yc_list = list(yc_slugs)
        yc_pages = dict(zip(yc_list, ex.map(fetch_yc_index, yc_list)))

    need_desc = []
    for job, parsed in located:
        if not parsed or parsed["p"] != "ycombinator":
            continue
        page = yc_pages.get(parsed["slug"])
        if page is not None and yc_listed(page, parsed["jid"]):
            need_desc.append((job, parsed))

    descriptions: dict[str, tuple[str, str | None]] = {}
    with ThreadPoolExecutor(workers) as ex:
        pairs = [(j.get("url") or "", p["jid"]) for j, p in need_desc]
        for (url, _tail), result in zip(pairs, ex.map(lambda it: fetch_yc_description(*it), pairs)):
            descriptions[url] = result

    counts = {"open": 0, "closed": 0, "changed": 0, "unknown": 0}
    for job, parsed in located:
        if not parsed or not parsed.get("slug"):
            counts["unknown"] += 1
            continue
        if parsed["p"] == "ycombinator":
            page = yc_pages.get(parsed["slug"])
            if page is None:
                counts["unknown"] += 1
                continue
            if not yc_listed(page, parsed["jid"]):
                counts[apply_check(job, board_ok=True, present=False, live_hash=None)] += 1
                continue
            state, text = descriptions.get(job.get("url") or "", ("unknown", None))
            if state == "closed":
                counts[apply_check(job, board_ok=True, present=False, live_hash=None)] += 1
                continue
            live = normalize.desc_hash(text) if state == "open" else None
            counts[apply_check(job, board_ok=True, present=True, live_hash=live)] += 1
            continue
        body = boards.get((parsed["p"], parsed["slug"]))
        if body is None:
            counts["unknown"] += 1
            continue
        text = body.get(parsed["jid"])
        counts[apply_check(job, board_ok=True, present=text is not None, live_hash=normalize.desc_hash(text))] += 1

    normalize.write(data, path)
    log.info("%s: %s", path, ", ".join(f"{k} {v}" for k, v in counts.items()))
    return counts
