"""Public Oracle Cloud candidate-experience boards.

The host is on the company's own careers page (jpmc.fa.oraclecloud.com).
Active sites and their requisitions are public JSON. Alumni and campus sites
are skipped when a general site is also active. PostedDate is a calendar day.
"""
from __future__ import annotations

import logging
import re
import time

import requests

from ..classify import classify_family, level_from_title, us_locations
from .ats import pay_from_text, strip_html, yoe_snippets

log = logging.getLogger("crawler")

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
_TIMEOUT = 30
_PAGE = 25
_CAP = 20000
_SKIP_SITE = re.compile(r"alumni|campus|intern|student|university|college", re.I)
_US = {"US", "USA"}


def active_sites(items: list[dict] | None) -> list[str]:
    """Site numbers that are live and not an alumni or campus board."""
    out = []
    for item in items or []:
        if item.get("StatusCode") != "ORA_ACTIVE":
            continue
        label = f"{item.get('SiteName') or ''} {item.get('SiteCode') or ''}"
        if _SKIP_SITE.search(label):
            continue
        number = (item.get("SiteNumber") or "").strip()
        if number and number not in out:
            out.append(number)
    return out


def _with_country(place: str, code: str) -> str:
    place = re.sub(r"\s+", " ", place or "").strip(" ,")
    if not place:
        return ""
    if (code or "").upper() in _US and not re.search(r"united states|\busa\b", place, re.I):
        return f"{place}, United States"
    return place


def oracle_places(job: dict) -> list[str]:
    """Primary location plus any extra offices on the same requisition."""
    places = []
    primary = _with_country(job.get("PrimaryLocation") or "", job.get("PrimaryLocationCountry") or "")
    if primary:
        places.append(primary)
    for extra in job.get("secondaryLocations") or []:
        if isinstance(extra, str):
            places.append(extra)
            continue
        if not isinstance(extra, dict):
            continue
        name = extra.get("Name") or extra.get("LocationName") or extra.get("PrimaryLocation") or ""
        code = extra.get("CountryCode") or extra.get("PrimaryLocationCountry") or ""
        place = _with_country(name, code)
        if place:
            places.append(place)
    return places


def oracle_rows(item: dict, host: str, site: str) -> list[dict]:
    """One search page. The caller drops titles and places that are out of scope."""
    out = []
    for job in item.get("requisitionList") or []:
        ident = str(job.get("Id") or "")
        title = re.sub(r"\s+", " ", job.get("Title") or "").strip()
        if not ident or not title:
            continue
        text = strip_html(" ".join(filter(None, [
            job.get("ShortDescriptionStr"),
            job.get("ExternalResponsibilitiesStr"),
            job.get("ExternalQualificationsStr"),
        ])))
        posted = job.get("PostedDate") or ""
        out.append({
            "id": ident,
            "t": title,
            "loc": oracle_places(job),
            "rem": bool(re.search(r"remote", job.get("WorkplaceType") or "", re.I)),
            "at": f"{posted}T12:00:00+00:00" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", posted) else None,
            "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{ident}",
            "pay": pay_from_text(text),
            "yoe": yoe_snippets(text),
            "desc": text,
        })
    return out


def keep_row(title: str, locs: list[str]) -> bool:
    if not classify_family(title) or level_from_title(title) == "junior":
        return False
    return bool(us_locations(locs))


def _get(url: str, params: dict) -> dict:
    response = requests.get(url, params=params, headers=_UA, timeout=_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _sites(host: str) -> list[dict]:
    payload = _get(
        f"https://{host}/hcmRestApi/resources/latest/recruitingCESites",
        {"onlyData": "true", "limit": 100},
    )
    return payload.get("items") or []


def _page(host: str, site: str, offset: int) -> dict:
    payload = _get(
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
        {
            "onlyData": "true",
            "expand": "requisitionList.secondaryLocations",
            "finder": f"findReqs;siteNumber={site},limit={_PAGE},offset={offset},sortBy=POSTING_DATES_DESC",
        },
    )
    items = payload.get("items") or []
    return items[0] if items else {}


def fetch(host: str, base: dict) -> list[dict]:
    """Every live site on one Oracle host. Ids are deduped across sites."""
    host = re.sub(r"^https?://", "", host or "").strip("/").split("/")[0]
    if not host:
        return []
    try:
        sites = active_sites(_sites(host))
    except (requests.RequestException, ValueError) as e:
        log.warning("oracle %s sites failed: %s", host, str(e)[:120])
        return []
    if not sites:
        log.info("oracle %s has no active general site", host)
        return []
    seen = set()
    raws = []
    for site in sites:
        offset = 0
        while offset < _CAP:
            try:
                item = _page(host, site, offset)
            except (requests.RequestException, ValueError) as e:
                log.warning("oracle %s %s offset %s failed: %s", host, site, offset, str(e)[:100])
                break
            reqs = item.get("requisitionList") or []
            if not reqs:
                break
            for row in oracle_rows(item, host, site):
                if row["id"] in seen or not keep_row(row["t"], row["loc"]):
                    continue
                seen.add(row["id"])
                raws.append({**base, "p": "oracle", **row})
            offset += len(reqs)
            total = int(item.get("TotalJobsCount") or 0)
            if offset >= total:
                break
            time.sleep(0.05)
        log.info("oracle %s %s kept %d so far", host, site, len(raws))
    return raws
