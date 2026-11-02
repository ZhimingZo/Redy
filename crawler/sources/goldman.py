"""Goldman Sachs publishes experienced roles on its own Higher site.

The list is a public GraphQL search at api-higher.gs.com. Campus roles are a
separate experience and are not requested. The role page is higher.gs.com/roles/{id}.
"""
from __future__ import annotations

import logging
import re
import time

import requests

from ..classify import classify_family, level_from_title, us_locations
from .ats import pay_from_text, strip_html, yoe_snippets

log = logging.getLogger("crawler")

_ENDPOINT = "https://api-higher.gs.com/gateway/api/v1/graphql"
_PAGE = 20
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://higher.gs.com",
    "Referer": "https://higher.gs.com/results",
}
_LIST = """
query GetRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items {
      roleId
      corporateTitle
      jobTitle
      locations { city state country }
      externalSource { sourceId }
    }
  }
}
"""
_DETAIL = """
query GetRoleById($externalSourceId: String!, $externalSourceFetch: Boolean) {
  role(externalSourceId: $externalSourceId, externalSourceFetch: $externalSourceFetch) {
    descriptionHtml
    compensation { minSalary maxSalary currency }
  }
}
"""


def goldman_places(item: dict) -> list[str]:
    out = []
    for loc in item.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        city = (loc.get("city") or "").strip()
        state = (loc.get("state") or "").strip()
        country = (loc.get("country") or "").strip()
        place = ", ".join(p for p in (city, state, country) if p)
        if place:
            out.append(place)
    return out


def keep(title: str, places: list[str]) -> bool:
    if not classify_family(title) or level_from_title(title) == "junior":
        return False
    return bool(us_locations(places))


def _post(query: str, variables: dict) -> dict:
    response = requests.post(
        _ENDPOINT,
        json={"query": query, "variables": variables},
        headers=_UA,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise ValueError(str(payload["errors"][0])[:160])
    return payload.get("data") or {}


def fetch(base: dict) -> list[dict]:
    variables = {
        "searchQueryInput": {
            "page": {"pageSize": _PAGE, "pageNumber": 0},
            "sort": {"sortStrategy": "POSTED_DATE", "sortOrder": "DESC"},
            "filters": [],
            "experiences": ["PROFESSIONAL"],
            "searchTerm": "",
        }
    }
    first = _post(_LIST, variables)
    search = first.get("roleSearch") or {}
    total = int(search.get("totalCount") or 0)
    items = list(search.get("items") or [])
    page = 1
    while len(items) < total and page < 80:
        variables["searchQueryInput"]["page"]["pageNumber"] = page
        try:
            nxt = _post(_LIST, variables)
        except (requests.RequestException, ValueError) as e:
            log.warning("goldman page %s failed: %s", page, str(e)[:120])
            break
        batch = (nxt.get("roleSearch") or {}).get("items") or []
        if not batch:
            break
        items.extend(batch)
        page += 1
        time.sleep(0.2)
    log.info("goldman listed %s, total %s", len(items), total)

    out = []
    for item in items:
        title = re.sub(r"\s+", " ", item.get("jobTitle") or "").strip()
        places = goldman_places(item)
        source = str((item.get("externalSource") or {}).get("sourceId") or "")
        if not source or not keep(title, places):
            continue
        desc = ""
        pay = None
        try:
            detail = _post(_DETAIL, {"externalSourceId": source, "externalSourceFetch": True})
            role = detail.get("role") or {}
            desc = strip_html(role.get("descriptionHtml") or "")
            comp = role.get("compensation") or {}
            if comp.get("minSalary") and comp.get("maxSalary"):
                pay = [int(comp["minSalary"]), int(comp["maxSalary"]), comp.get("currency") or "USD", "year"]
        except (requests.RequestException, ValueError):
            desc = ""
        time.sleep(0.1)
        out.append({
            **base,
            "p": "goldman",
            "id": source,
            "t": title,
            "loc": places,
            "ctry": "",
            "rem": any(re.search(r"remote", p, re.I) for p in places),
            "at": None,
            "url": f"https://higher.gs.com/roles/{source}",
            "pay": pay or pay_from_text(desc),
            "yoe": yoe_snippets(desc),
            "desc": desc,
        })
    log.info("goldman kept %s", len(out))
    return out
