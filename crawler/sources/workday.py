"""Public Workday career-site adapter.

A board is host + site path, for example
nvidia.wd5.myworkdayjobs.com / NVIDIAExternalCareerSite. The site path is not
guessable from the host: the host alone answers 406. It is written on the
company's own careers page (Nvidia hides it on jobs.nvidia.com/careers).

The jobs API returns at most 20 per page and stops at 2,000 rows. Past that
cap the call is repeated with the United States location facet, then with a
job family, so US engineering roles are not cut off. Posted dates come from
startDate on the job detail, not from the relative "Posted 30+ Days Ago" label.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from ..classify import classify_family, level_from_title, us_locations
from ..locations import STATES, _STATE_BY_NAME
from .ats import TIMEOUT, UA, pay_from_text, strip_html, yoe_snippets

log = logging.getLogger("crawler")

_PAGE = 20
_CAP = 2000
_SITE = re.compile(
    r"([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Za-z]{2}/)?([A-Za-z0-9][A-Za-z0-9_-]*)",
    re.I,
)
_SKIP_SITE = {"login", "job", "jobs", "search", "userhome"}
_LOCALE = re.compile(r"^[a-z]{2}-[A-Za-z]{2}$")
_CAMPUS = re.compile(r"college|university|intern|campus|student|earlycareer|early.career", re.I)
_US_PLACE = re.compile(r"united states|^usa$|^u\.s\.a?\.?$", re.I)
_COUNTRY_PARAMS = {"locationHierarchy1", "locationCountry"}
_MULTI = re.compile(r"\d+\s+locations", re.I)
_WD_LOC = re.compile(r"^(?:US|USA),\s*([A-Z]{2}),\s*(.+)$", re.I)
_WD_LOC_LOOSE = re.compile(r"^(?:US|USA),\s*(.+)$", re.I)


def discover_sites(text: str) -> list[tuple[str, str]]:
    """(host, site) pairs mentioned in a careers page or a Workday URL."""
    blob = (text or "").replace("\\u002f", "/").replace("\\/", "/")
    out = []
    for m in _SITE.finditer(blob):
        site = m.group(2)
        if site.lower() in _SKIP_SITE or _LOCALE.match(site):
            continue
        pair = (m.group(1).lower(), site)
        if pair not in out:
            out.append(pair)
    return out


def choose_sites(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop campus boards when the company also has a general external board."""
    if not pairs:
        return []
    general = [p for p in pairs if not _CAMPUS.search(p[1])]
    return general or pairs


def _state_token(token: str) -> str | None:
    t = token.strip(" .")
    if len(t) == 2 and t.upper() in STATES:
        return t.upper()
    return _STATE_BY_NAME.get(t.lower())


def _city_only(city: str) -> str:
    city = re.sub(r"\s*\([^)]*\)", "", city or "")
    city = re.split(r"\s+\d", city, maxsplit=1)[0]
    return city.strip(" -")


def workday_place(text: str) -> str:
    """Workday location strings -> 'City, ST' when the board names a US place.

    Boards do not share one format: 'US, CA, Santa Clara', 'USA - CA - San Jose',
    'United States - California - Foster City', 'USA MD - Sparks - 7 Loveton'.
    """
    text = (text or "").strip()
    if not text:
        return text
    m = _WD_LOC.match(text)
    if m:
        return f"{_city_only(m.group(2))}, {m.group(1).upper()}"
    m = _WD_LOC_LOOSE.match(text)
    if m:
        return m.group(1).strip()
    m = re.match(r"^([^,]+),\s*([A-Z]{2})\b", text)
    if m and m.group(2).upper() in STATES:
        return f"{m.group(1).strip()}, {m.group(2).upper()}"
    if " - " not in text:
        m = re.match(r"^(?:USA|US)\s+([A-Z]{2})\s+(.+)$", text)
        if m and m.group(1).upper() in STATES:
            return f"{_city_only(m.group(2))}, {m.group(1).upper()}"
    parts = [p.strip() for p in re.split(r"\s+-\s+", text) if p.strip()]
    if parts and re.fullmatch(r"USA|US|United States(?: of America)?", parts[0], re.I):
        parts = parts[1:]
    if not parts:
        return text
    m = re.match(r"^(?:USA|US)\s+([A-Z]{2})$", parts[0])
    if m and m.group(1).upper() in STATES and len(parts) >= 2:
        return f"{_city_only(parts[1])}, {m.group(1).upper()}"
    st = _state_token(parts[0])
    if st and len(parts) >= 2:
        return f"{_city_only(parts[1])}, {st}"
    placed = _city_before_state(text)
    return placed or text


def _city_before_state(text: str) -> str | None:
    """'Tampa Florida United States' and 'New York New York United States'."""
    m = re.match(r"^(.+?)\s+United States(?:\s+of\s+America)?$", text, re.I)
    if not m:
        return None
    rest = m.group(1).strip()
    low = rest.lower()
    for name in sorted(_STATE_BY_NAME, key=len, reverse=True):
        if low == name or low.endswith(" " + name):
            city = rest[: len(rest) - len(name)].strip()
            st = _STATE_BY_NAME[name]
            return f"{_city_only(city)}, {st}" if city else STATES.get(st, rest)
    return None


def _iter_facets(nodes):
    for node in nodes or []:
        nested = [v for v in (node.get("values") or []) if isinstance(v, dict)]
        if nested and any(v.get("values") for v in nested):
            yield from _iter_facets(nested)
            continue
        param = node.get("facetParameter")
        if param and nested and any(v.get("id") for v in nested):
            for v in nested:
                if v.get("id"):
                    yield param, v["id"], v.get("descriptor") or "", int(v.get("count") or 0)


def _is_country_param(param: str) -> bool:
    """Boards name the country facet differently: locationCountry, Country_and_Jurisdiction."""
    key = (param or "").lower()
    return key in {p.lower() for p in _COUNTRY_PARAMS} or "country" in key


def _us_and_remote(facets) -> list[dict]:
    """Facet filters that keep US rows, plus a remote slice when the board has one."""
    plans = []
    for param, fid, desc, count in _iter_facets(facets):
        if count <= 0:
            continue
        if _is_country_param(param) and _US_PLACE.search(desc.strip()):
            plans.append({param: [fid]})
        elif re.fullmatch(r"remote", desc.strip(), re.I) and count < _CAP:
            plans.append({param: [fid]})
    return plans


def _is_family_param(param: str) -> bool:
    """jobFamilyGroup on most boards, Job_Family_Group on T-Mobile."""
    return (param or "").lower().replace("_", "") == "jobfamilygroup"


def _family_plans(facets) -> list[dict]:
    plans = []
    seen = set()
    for param, fid, _desc, count in _iter_facets(facets):
        if not _is_family_param(param) or not fid or count <= 0:
            continue
        key = (param, fid)
        if key in seen:
            continue
        seen.add(key)
        plans.append({param: [fid]})
    return plans


def _post(host: str, tenant: str, site: str, facets: dict, offset: int) -> dict | None:
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    body = {"appliedFacets": facets, "limit": _PAGE, "offset": offset, "searchText": ""}
    headers = {**UA, "Accept": "application/json", "Content-Type": "application/json"}
    for attempt in (1, 2, 3, 4):
        try:
            r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as e:
            if attempt == 4:
                log.warning("workday %s/%s list failed: %s", host, site, str(e)[:120])
                return None
            time.sleep(1.5 * attempt)
            continue
        if r.status_code in (429, 500, 502, 503) and attempt < 4:
            time.sleep(1.5 * attempt)
            continue
        if r.status_code != 200:
            log.warning("workday %s/%s list HTTP %s", host, site, r.status_code)
            return None
        try:
            return r.json()
        except ValueError:
            return None
    return None


def list_postings(host: str, site: str, poster=_post) -> list[dict]:
    """Every posting the API will return. Splits facets once the 2,000 cap bites."""
    tenant = host.split(".")[0]
    seen: set[str] = set()
    out = []

    def add(rows):
        for row in rows or []:
            path = row.get("externalPath") or ""
            if not path or path in seen:
                continue
            seen.add(path)
            out.append(row)

    def page(facets: dict, depth: int, families: list[dict] | None = None) -> None:
        first = poster(host, tenant, site, facets, 0)
        if not first:
            return
        total = int(first.get("total") or 0)
        found_families = _family_plans(first.get("facets") or []) or families or []
        if total >= _CAP and depth < 4:
            facet_nodes = first.get("facets") or []
            country_applied = any(_is_country_param(k) for k in facets)
            family_applied = any(_is_family_param(k) for k in facets)
            if depth == 0:
                plans = _us_and_remote(facet_nodes) or found_families
            elif not family_applied:
                plans = found_families
            elif not country_applied:
                plans = _us_and_remote(facet_nodes)
            else:
                plans = []
            if plans:
                for extra in plans:
                    page({**facets, **extra}, depth + 1, found_families)
                return
            log.warning("workday %s/%s capped at %s rows", host, site, total)
        add(first.get("jobPostings"))
        offset = _PAGE
        misses = 0
        while offset < total and offset < _CAP:
            nxt = poster(host, tenant, site, facets, offset)
            rows = (nxt or {}).get("jobPostings") or []
            if not rows:
                misses += 1
                if misses >= 3:
                    log.warning("workday %s/%s listed %s of %s before a page failed", host, site, len(out), total)
                    break
                time.sleep(1.5 * misses)
                continue
            misses = 0
            add(rows)
            offset += _PAGE

    page({}, 0)
    return out


def worth_detail(posting: dict) -> bool:
    """List rows that cannot be a US eng/PM role never get a detail request."""
    title = posting.get("title") or ""
    if not classify_family(title):
        return False
    if level_from_title(title) == "junior":
        return False
    loc = posting.get("locationsText") or ""
    if not loc or _MULTI.search(loc) or re.search(r"remote", loc, re.I):
        return True
    return bool(us_locations([workday_place(loc)]))


def posted_at(info: dict, today=None) -> str | None:
    """ISO time. startDate is a calendar day; postedOn is only a relative label."""
    start = info.get("startDate") or ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
        return f"{start}T12:00:00+00:00"
    text = info.get("postedOn") or ""
    day = today or datetime.now(timezone.utc).date()
    if re.search(r"today", text, re.I):
        when = day
    elif re.search(r"yesterday", text, re.I):
        when = day - timedelta(days=1)
    else:
        m = re.search(r"(\d+)\+?\s*days?", text, re.I)
        if not m:
            return None
        when = day - timedelta(days=int(m.group(1)))
    return f"{when.isoformat()}T12:00:00+00:00"


def _detail(host: str, tenant: str, site: str, path: str) -> dict | None:
    url = f"https://{host}/wday/cxs/{tenant}/{site}{path}"
    headers = {**UA, "Accept": "application/json"}
    for attempt in (1, 2, 3, 4):
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as e:
            if attempt == 4:
                log.warning("workday detail %s failed: %s", path, str(e)[:120])
                return None
            time.sleep(1.5 * attempt)
            continue
        if r.status_code in (429, 500, 502, 503) and attempt < 4:
            time.sleep(1.5 * attempt)
            continue
        if r.status_code != 200:
            return None
        try:
            return (r.json().get("jobPostingInfo") or {}) or None
        except ValueError:
            return None
    return None


def _locations(info: dict) -> list[str]:
    raw = [info.get("location") or ""]
    for extra in info.get("additionalLocations") or []:
        if isinstance(extra, str):
            raw.append(extra)
        elif isinstance(extra, dict):
            raw.append(extra.get("descriptor") or extra.get("location") or "")
    return [workday_place(x) for x in raw if workday_place(x)]


def records(host: str, site: str, base: dict, postings: list[dict], detail_fn=None) -> list[dict]:
    """Turn list rows into raw job records. detail_fn is injectable for tests."""
    tenant = host.split(".")[0]
    getter = detail_fn or (lambda path: _detail(host, tenant, site, path))
    out = []
    for row in postings:
        if not worth_detail(row):
            continue
        path = row.get("externalPath") or ""
        info = getter(path) or {}
        if not info and detail_fn is None:
            continue
        desc = strip_html(info.get("jobDescription"))
        country = info.get("country") or {}
        ctry = country.get("descriptor") if isinstance(country, dict) else ""
        req = info.get("jobReqId") or (row.get("bulletFields") or [None])[0] or path.rsplit("/", 1)[-1]
        url = info.get("externalUrl") or f"https://{host}/{site}{path}"
        locs = _locations(info) or ([workday_place(row.get("locationsText") or "")] if row.get("locationsText") else [])
        out.append({
            **base,
            "p": "workday",
            "id": str(req),
            "t": info.get("title") or row.get("title") or "",
            "loc": locs,
            "ctry": ctry or "",
            "rem": any(re.search(r"remote", x or "", re.I) for x in locs),
            "at": posted_at(info or {"postedOn": row.get("postedOn")}),
            "url": url,
            "pay": pay_from_text(desc),
            "yoe": yoe_snippets(desc),
            "desc": desc,
        })
    return out


def fetch(host: str, site: str, base: dict) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor

    postings = list_postings(host, site)
    picked = [row for row in postings if worth_detail(row)]
    log.info("workday %s/%s: %d listed, %d worth a detail", host, site, len(postings), len(picked))
    if not picked:
        return []
    tenant = host.split(".")[0]
    paths = [(row, row.get("externalPath") or "") for row in picked]

    def one(item):
        row, path = item
        info = _detail(host, tenant, site, path)
        return row, info

    out = []
    done = 0
    with ThreadPoolExecutor(8) as ex:
        for row, info in ex.map(one, paths):
            done += 1
            if done % 100 == 0:
                log.info("workday %s/%s details %d/%d", host, site, done, len(paths))
            if not info:
                continue
            out.extend(records(host, site, base, [row], detail_fn=lambda _p, info=info: info))
    return out
