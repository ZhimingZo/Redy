"""One reader per company that publishes jobs on its own site.

Greenhouse, Lever, Ashby, and Workday share a shape. The companies here do not,
so each function knows that company's public list. The records they return are
the same shape as the other adapters, and the usual US / engineering / product
filter runs afterwards.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape

import requests

from ..classify import classify_family, level_from_title, us_locations
from ..locations import STATES, _state_code, pretty_locations
from .ats import pay_from_text, strip_html, yoe_snippets

log = logging.getLogger("crawler")

_UA = {
    "User-Agent": "Mozilla/5.0 (compatible; RedyBot/0.1; +https://redy.fyi)",
    "Accept": "text/html,application/json",
}
_TIMEOUT = 30

_COMPANIES = {
    "google": {"co": "Alphabet Inc.", "cslug": "google", "dom": "google.com", "src": "tech"},
    "amazon": {"co": "Amazon", "cslug": "amazon", "dom": "amazon.com", "src": "tech"},
    "apple": {"co": "Apple Inc.", "cslug": "apple", "dom": "apple.com", "src": "tech"},
    "netflix": {"co": "Netflix", "cslug": "netflix", "dom": "netflix.com", "src": "tech"},
    "meta": {"co": "Meta Platforms", "cslug": "meta", "dom": "meta.com", "src": "tech"},
    "tesla": {"co": "Tesla, Inc.", "cslug": "tesla", "dom": "tesla.com", "src": "tech"},
    "microsoft": {"co": "Microsoft", "cslug": "microsoft", "dom": "microsoft.com", "src": "tech"},
    "salesforce": {"co": "Salesforce", "cslug": "salesforce", "dom": "salesforce.com", "src": "tech"},
    "paypal": {"co": "PayPal", "cslug": "paypal", "dom": "paypal.com", "src": "tech"},
    "intuit": {"co": "Intuit", "cslug": "intuit", "dom": "intuit.com", "src": "tech"},
    "synopsys": {"co": "Synopsys", "cslug": "synopsys", "dom": "synopsys.com", "src": "tech"},
    "supermicro": {"co": "Supermicro", "cslug": "supermicro", "dom": "supermicro.com", "src": "tech"},
    "anduril": {"co": "Anduril", "cslug": "anduril", "dom": "anduril.com", "src": "tech"},
    "qualcomm": {"co": "Qualcomm", "cslug": "qualcomm", "dom": "qualcomm.com", "src": "tech"},
    "appliedmaterials": {"co": "Applied Materials", "cslug": "appliedmaterials", "dom": "appliedmaterials.com", "src": "tech"},
    "hp": {"co": "HP Inc.", "cslug": "hp", "dom": "hp.com", "src": "tech"},
    "autodesk": {"co": "Autodesk", "cslug": "autodesk", "dom": "autodesk.com", "src": "tech"},
    "micron": {"co": "Micron Technology", "cslug": "micron", "dom": "micron.com", "src": "tech"},
    "lam": {"co": "Lam Research", "cslug": "lam", "dom": "lamresearch.com", "src": "tech"},
    "netapp": {"co": "NetApp", "cslug": "netapp", "dom": "netapp.com", "src": "tech"},
    "amd": {"co": "Advanced Micro Devices", "cslug": "amd", "dom": "amd.com", "src": "tech"},
    "lockheed": {"co": "Lockheed Martin", "cslug": "lockheed", "dom": "lockheedmartin.com", "src": "traditional"},
    "eaton": {"co": "Eaton Corporation", "cslug": "eaton", "dom": "eaton.com", "src": "traditional"},
    "boston": {"co": "Boston Scientific", "cslug": "boston", "dom": "bostonscientific.com", "src": "traditional"},
    "deere": {"co": "Deere & Company", "cslug": "deere", "dom": "deere.com", "src": "traditional"},
    "dexcom": {"co": "Dexcom", "cslug": "dexcom", "dom": "dexcom.com", "src": "traditional"},
    "chipotle": {"co": "Chipotle Mexican Grill", "cslug": "chipotle", "dom": "chipotle.com", "src": "traditional"},
    "unitedhealth": {"co": "UnitedHealth Group", "cslug": "unitedhealth", "dom": "unitedhealthgroup.com", "src": "traditional"},
    "l3harris": {"co": "L3Harris", "cslug": "l3harris", "dom": "l3harris.com", "src": "traditional"},
    "keurig": {"co": "Keurig Dr Pepper", "cslug": "keurig", "dom": "keurigdrpepper.com", "src": "traditional"},
    "hershey": {"co": "Hershey Company", "cslug": "hershey", "dom": "thehersheycompany.com", "src": "traditional"},
    "unionpacific": {"co": "Union Pacific Corporation", "cslug": "unionpacific", "dom": "up.com", "src": "traditional"},
}


def _get(url: str, **kwargs) -> requests.Response:
    return requests.get(url, headers=_UA, timeout=_TIMEOUT, **kwargs)


def _stamp(text: str) -> str | None:
    text = (text or "").strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            day = datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
        return f"{day.isoformat()}T12:00:00+00:00"
    return None


def _json_string(html: str, key: str) -> str:
    token = f'"{key}":"'
    i = html.find(token)
    if i < 0:
        return ""
    i += len(token)
    out = []
    while i < len(html):
        ch = html[i]
        if ch == "\\":
            nxt = html[i + 1] if i + 1 < len(html) else ""
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/"}.get(nxt, nxt))
            i += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _record(which: str, ident: str, title: str, locs: list[str], url: str, posted: str | None, desc: str) -> dict | None:
    title = re.sub(r"\s+", " ", unescape(title or "")).strip()
    if not classify_family(title) or level_from_title(title) == "junior":
        return None
    places = [p.strip() for p in locs if p and p.strip()]
    if not us_locations(places):
        return None
    text = strip_html(desc)
    return {
        **_COMPANIES[which],
        "p": which,
        "id": str(ident),
        "t": title,
        "loc": places,
        "ctry": "",
        "rem": any(re.search(r"remote", p, re.I) for p in places),
        "at": posted,
        "url": url,
        "pay": pay_from_text(text),
        "yoe": yoe_snippets(text),
        "desc": text,
    }


def parse_apple(html: str) -> list[dict]:
    """Job cards from a US search page. The id keeps the location suffix."""
    out = []
    seen = set()
    for m in re.finditer(r'href="(/en-us/details/([^"/]+)/[^"]*)"', html):
        ident = m.group(2)
        if ident in seen:
            continue
        window = html[m.start(): m.start() + 1500]
        title = re.search(r">([^<]+)</a>", window)
        posted = re.search(r'job-posted-date[^>]*>([^<]+)<', window)
        loc = re.search(r'search-store-name-container-\d+">([^<]*)<', window)
        if not title:
            continue
        seen.add(ident)
        city = unescape(loc.group(1)).strip() if loc else ""
        places = [city] if city else ["United States"]
        if city and not pretty_locations(places)[0]:
            places = [city, "United States"]
        out.append({
            "id": ident,
            "title": unescape(title.group(1)),
            "locs": places,
            "posted": _stamp(posted.group(1)) if posted else None,
            "url": "https://jobs.apple.com" + m.group(1).split("?")[0],
        })
    return out


def parse_google(html: str) -> list[dict]:
    out = []
    for part in html.split('class="lLd3Je"')[1:]:
        ident = re.search(r"ssk='\d+:(\d+)'", part)
        title = re.search(r'class="QJPWVe">(.*?)</h3>', part, re.S)
        if not ident or not title:
            continue
        locs = [unescape(x) for x in re.findall(r'class="r0wTof[^"]*"[^>]*>([^<]+)</span>', part)]
        out.append({
            "id": ident.group(1),
            "title": unescape(re.sub(r"<[^>]+>", "", title.group(1))),
            "locs": locs,
            "posted": None,
            "url": f"https://www.google.com/about/careers/applications/jobs/results/{ident.group(1)}",
        })
    return out


def parse_meta(html: str) -> list[dict]:
    """One page of metacareers.com/jobsearch. Ten cards, then the next page."""
    out = []
    seen = set()
    for m in re.finditer(r'href="(/profile/job_details/(\d+))"', html):
        ident = m.group(2)
        if ident in seen:
            continue
        window = html[m.end(): m.end() + 2500]
        title = re.search(r"<h3[^>]*>(.*?)</h3>", window, re.S)
        locs = re.findall(r">([A-Z][^<]{1,40},\s*[A-Z]{2})<", window)
        if not title:
            continue
        seen.add(ident)
        out.append({
            "id": ident,
            "title": unescape(re.sub(r"<[^>]+>", "", title.group(1))),
            "locs": locs or ["United States"],
            "posted": None,
            "url": "https://www.metacareers.com" + m.group(1),
        })
    return out


def _keep_rows(which: str, rows: list[dict], desc_of=None) -> list[dict]:
    """Descriptions are fetched only after the title and location already qualify."""
    picked = [
        row for row in rows
        if _record(which, row["id"], row["title"], row["locs"], row["url"], row.get("posted"), "") is not None
    ]
    descs = {}
    if desc_of and picked:
        def one(row):
            try:
                return row["id"], desc_of(row) or ""
            except Exception as e:
                log.warning("%s detail %s failed: %s", which, row["id"], str(e)[:80])
                return row["id"], ""

        with ThreadPoolExecutor(8) as ex:
            descs = dict(ex.map(one, picked))
    out = []
    for row in picked:
        rec = _record(
            which, row["id"], row["title"], row["locs"], row["url"], row.get("posted"),
            descs.get(row["id"]) or row.get("desc") or "",
        )
        if rec:
            out.append(rec)
    return out


def fetch_apple() -> list[dict]:
    rows, seen = [], set()
    for page in range(1, 45):
        url = f"https://jobs.apple.com/en-us/search?location=united-states-USA&page={page}"
        try:
            html = _get(url).text
        except requests.RequestException as e:
            log.warning("apple page %s failed: %s", page, str(e)[:100])
            break
        found = [row for row in parse_apple(html) if row["id"] not in seen]
        if not found:
            break
        seen.update(row["id"] for row in found)
        rows.extend(found)
        time.sleep(0.15)
    log.info("apple: %d US listings", len(rows))

    def desc_of(row):
        try:
            return _json_string(_get(row["url"]).text, "jobSummary")
        except requests.RequestException:
            return ""

    return _keep_rows("apple", rows, desc_of)


def fetch_amazon() -> list[dict]:
    """Software category, plus a product-manager search. The list includes the description."""
    queries = [
        {"country": "USA", "category[]": "software-development"},
        {"country": "USA", "base_query": "product manager"},
    ]
    seen, out = set(), []
    for params in queries:
        offset = 0
        while offset < 5000:
            try:
                payload = _get(
                    "https://www.amazon.jobs/en/search.json",
                    params={**params, "offset": offset, "result_limit": 100},
                ).json()
            except (requests.RequestException, ValueError) as e:
                log.warning("amazon offset %s failed: %s", offset, str(e)[:100])
                break
            jobs = payload.get("jobs") or []
            if not jobs:
                break
            for job in jobs:
                ident = str(job.get("id_icims") or job.get("id") or "")
                if not ident or ident in seen or job.get("is_intern") or job.get("university_job"):
                    continue
                if (job.get("country_code") or "").upper() not in ("USA", "US", ""):
                    continue
                seen.add(ident)
                city, state = (job.get("city") or "").strip(), (job.get("state") or "").strip()
                loc = f"{city}, {state}" if city and state else (job.get("location") or city)
                posted = _stamp(job.get("posted_date") or "")
                desc = " ".join(filter(None, [job.get("description"), job.get("basic_qualifications"), job.get("preferred_qualifications")]))
                path = job.get("job_path") or f"/en/jobs/{ident}"
                rec = _record(
                    "amazon", ident, job.get("title") or "",
                    [loc] if loc else [],
                    "https://www.amazon.jobs" + path,
                    posted, desc,
                )
                if rec:
                    out.append(rec)
            offset += len(jobs)
            if offset >= int(payload.get("hits") or 0):
                break
            time.sleep(0.1)
    log.info("amazon: %d kept", len(out))
    return out


def fetch_google() -> list[dict]:
    rows, seen = [], set()
    for page in range(1, 120):
        url = "https://www.google.com/about/careers/applications/jobs/results/"
        try:
            html = _get(url, params={"location": "United States", "hl": "en", "page": page}).text
        except requests.RequestException as e:
            log.warning("google page %s failed: %s", page, str(e)[:100])
            break
        found = [row for row in parse_google(html) if row["id"] not in seen]
        if not found:
            break
        seen.update(row["id"] for row in found)
        rows.extend(found)
        if page % 20 == 0:
            log.info("google page %s, %d listings", page, len(rows))
        time.sleep(0.1)
    log.info("google: %d US listings", len(rows))
    return _keep_rows("google", rows)


def fetch_netflix() -> list[dict]:
    """The public list is Netflix's Eightfold board at explore.jobs.netflix.net."""
    rows = []
    start, total = 0, 1
    while start < total and start < 2000:
        try:
            payload = _get(
                "https://explore.jobs.netflix.net/api/apply/v2/jobs",
                params={"domain": "netflix.com", "start": start, "num": 20, "location": "United States"},
            ).json()
        except (requests.RequestException, ValueError) as e:
            log.warning("netflix start %s failed: %s", start, str(e)[:100])
            break
        total = int(payload.get("count") or 0)
        positions = payload.get("positions") or []
        if not positions:
            break
        for job in positions:
            locs = job.get("locations") or ([job["location"]] if job.get("location") else [])
            when = job.get("t_create")
            posted = None
            if isinstance(when, (int, float)) and when > 0:
                posted = datetime.fromtimestamp(when, timezone.utc).date().isoformat() + "T12:00:00+00:00"
            ident = str(job.get("id") or "")
            rows.append({
                "id": ident,
                "title": job.get("name") or "",
                "locs": locs,
                "posted": posted,
                "url": job.get("canonicalPositionUrl") or f"https://explore.jobs.netflix.net/careers/job/{ident}",
            })
        start += len(positions)
        time.sleep(0.1)
    log.info("netflix: %d listings", len(rows))

    def desc_of(row):
        try:
            payload = _get(
                f"https://explore.jobs.netflix.net/api/apply/v2/jobs/{row['id']}",
                params={"domain": "netflix.com"},
            ).json()
        except (requests.RequestException, ValueError):
            return ""
        return payload.get("job_description") or ""

    return _keep_rows("netflix", rows, desc_of)


def fetch_meta() -> list[dict]:
    """metacareers.com serves ten jobs per page. A blocked response stops the walk."""
    rows, seen = [], set()
    for page in range(1, 140):
        try:
            response = _get("https://www.metacareers.com/jobsearch/", params={"page": page})
        except requests.RequestException as e:
            log.warning("meta page %s failed: %s", page, str(e)[:100])
            break
        if response.status_code != 200:
            log.warning("meta page %s HTTP %s", page, response.status_code)
            break
        found = [row for row in parse_meta(response.text) if row["id"] not in seen]
        if not found:
            break
        seen.update(row["id"] for row in found)
        rows.extend(found)
        time.sleep(0.15)
    log.info("meta: %d listings", len(rows))
    return _keep_rows("meta", rows)


def _tesla_place(loc: str) -> str:
    """'Fremont, California' -> 'Fremont, CA', so the US filter can see the state."""
    parts = [p.strip() for p in (loc or "").split(",") if p.strip()]
    if len(parts) >= 2:
        code = _state_code(parts[-1])
        if code:
            return f"{parts[-2]}, {code}"
    return loc


def tesla_rows(state: dict) -> list[dict]:
    """Listings from /cua-api/apps/careers/state. Locations are 'City, State'."""
    places = (state.get("lookup") or {}).get("locations") or {}
    out = []
    for row in state.get("listings") or []:
        loc = _tesla_place(places.get(str(row.get("l"))) or "")
        ident = str(row.get("id") or "")
        if not ident:
            continue
        out.append({
            "id": ident,
            "title": row.get("t") or "",
            "locs": [loc] if loc else [],
            "posted": None,
            "url": f"https://www.tesla.com/careers/search/job/{ident}",
        })
    return out


def fetch_tesla() -> list[dict]:
    try:
        response = _get("https://www.tesla.com/cua-api/apps/careers/state")
    except requests.RequestException as e:
        log.warning("tesla failed: %s", str(e)[:100])
        return []
    if response.status_code != 200:
        log.warning("tesla HTTP %s", response.status_code)
        return []
    try:
        state = response.json()
    except ValueError:
        return []
    rows = tesla_rows(state)
    log.info("tesla: %d listings", len(rows))
    return _keep_rows("tesla", rows)


_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://apply.careers.microsoft.com/careers",
}


def microsoft_places(locs: list[str] | None) -> list[str]:
    """'Redmond, WA, US' -> 'Redmond, WA'. A bare country token is not a city."""
    out = []
    for loc in locs or []:
        loc = re.sub(r"\s+", " ", loc or "").strip()
        if not loc or loc.upper() in {"US", "USA"} or re.search(r"multiple location", loc, re.I):
            continue
        if loc.endswith(", US"):
            loc = loc[:-4].strip(" ,")
        if loc:
            out.append(loc)
    return out or ["United States"]


def fetch_microsoft(start: int = 0) -> list[dict]:
    """Public search on apply.careers.microsoft.com. The list is already limited to the US."""
    rows, seen = [], set()
    total = start + 1
    while start < total and start < 5000:
        payload = None
        for attempt in (1, 2, 3, 4):
            try:
                response = requests.get(
                    "https://apply.careers.microsoft.com/api/pcsx/search",
                    params={"domain": "microsoft.com", "query": "", "location": "United States", "start": start, "num": 20},
                    headers=_BROWSER,
                    timeout=_TIMEOUT,
                )
            except requests.RequestException as e:
                log.warning("microsoft start %s failed: %s", start, str(e)[:100])
                break
            if response.status_code == 429 and attempt < 4:
                time.sleep(30 * attempt)
                continue
            try:
                payload = response.json()
            except ValueError:
                log.warning("microsoft start %s HTTP %s", start, response.status_code)
                payload = None
            break
        if not payload:
            break
        data = payload.get("data") or {}
        total = int(data.get("count") or 0)
        positions = data.get("positions") or []
        fresh = [job for job in positions if str(job.get("id") or "") not in seen]
        if not fresh:
            break
        for job in fresh:
            ident = str(job.get("id") or "")
            seen.add(ident)
            when = job.get("postedTs")
            posted = None
            if isinstance(when, (int, float)) and when > 0:
                posted = datetime.fromtimestamp(when, timezone.utc).date().isoformat() + "T12:00:00+00:00"
            rows.append({
                "id": ident,
                "title": job.get("name") or "",
                "locs": microsoft_places(job.get("standardizedLocations") or job.get("locations")),
                "posted": posted,
                "url": f"https://apply.careers.microsoft.com/careers/job/{ident}",
            })
        start += len(positions)
        time.sleep(0.35)
    log.info("microsoft: %d US listings", len(rows))

    def desc_of(row):
        for attempt in (1, 2):
            try:
                response = requests.get(
                    "https://apply.careers.microsoft.com/api/pcsx/position_details",
                    params={"domain": "microsoft.com", "position_id": row["id"]},
                    headers=_BROWSER,
                    timeout=_TIMEOUT,
                )
            except requests.RequestException:
                return ""
            if response.status_code == 429 and attempt == 1:
                time.sleep(6)
                continue
            try:
                payload = response.json()
            except ValueError:
                return ""
            return ((payload.get("data") or {}).get("jobDescription")) or ""
        return ""

    return _keep_rows("microsoft", rows, desc_of)


def salesforce_places(job: dict) -> list[str]:
    """'Virginia - Mclean' -> 'Mclean, VA'. Non-US countries contribute nothing."""
    countries = [str(c) for c in (job.get("Countries") or [])]
    if countries and not any(re.search(r"united states", c, re.I) for c in countries):
        return []
    places = []
    primary = job.get("Job_Requisition_Primary_Location") or ""
    if " - " in primary:
        state, city = [part.strip() for part in primary.split(" - ", 1)]
        code = _state_code(state)
        if code:
            places.append(f"Remote, {code}" if re.search(r"remote", city, re.I) else f"{city}, {code}")
    for city in job.get("Locations") or []:
        city = str(city).strip()
        if not city:
            continue
        places.append("Remote, United States" if re.search(r"remote", city, re.I) else city)
    return places


def salesforce_rows(entries: list[dict]) -> list[dict]:
    out, seen = [], set()
    for job in entries:
        ident = str(job.get("Job_Requisition_Ref_ID") or "")
        if not ident or ident in seen:
            continue
        seen.add(ident)
        posted = job.get("External_Job_Posting_Start_Date") or ""
        out.append({
            "id": ident,
            "title": job.get("Job_Posting_Title") or "",
            "locs": salesforce_places(job),
            "posted": f"{posted}T12:00:00+00:00" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", posted) else None,
            "url": job.get("External_Job_Posting_Site") or f"https://salesforce.wd12.myworkdayjobs.com/External_Career_Site/job/{ident}",
            "desc": job.get("Job_Description") or "",
        })
    return out


def fetch_salesforce() -> list[dict]:
    """The careers page publishes two JSON files. They are the same requisitions, so ids collapse."""
    entries = []
    for name in ("jobs_1.json", "jobs_2.json"):
        try:
            payload = requests.get(
                f"https://a.sfdcstatic.com/digital/xsf/careers/prod/{name}",
                headers=_BROWSER,
                timeout=60,
            ).json()
        except (requests.RequestException, ValueError) as e:
            log.warning("salesforce %s failed: %s", name, str(e)[:100])
            continue
        entries.extend(payload.get("Report_Entry") or [])
    rows = salesforce_rows(entries)
    log.info("salesforce: %d listings", len(rows))
    return _keep_rows("salesforce", rows)


_PAYPAL_CITY = re.compile(r"^(.+),\s*([A-Z]{2}),\s*US$")


def paypal_places(locs: list[str] | None) -> list[str]:
    """Keep 'San Jose, CA, US'. Drop bare 'CA,US' state codes the board repeats for remote roles."""
    out = []
    for loc in locs or []:
        match = _PAYPAL_CITY.match(re.sub(r"\s+", " ", loc or "").strip())
        if not match:
            continue
        city = match.group(1).strip()
        if city.lower() in {"washington dc", "washington d.c."}:
            city = "Washington"
        place = f"{city}, {match.group(2)}"
        if place not in out:
            out.append(place)
    return out or ["United States"]


def fetch_paypal() -> list[dict]:
    """Public search on paypal.eightfold.ai. The list is already limited to the US."""
    headers = {**_BROWSER, "Referer": "https://paypal.eightfold.ai/careers"}
    rows, seen = [], set()
    start, total = 0, 1
    while start < total and start < 5000:
        payload = None
        for attempt in (1, 2, 3):
            try:
                response = requests.get(
                    "https://paypal.eightfold.ai/api/pcsx/search",
                    params={"domain": "paypal.com", "query": "", "location": "United States", "start": start, "num": 20},
                    headers=headers,
                    timeout=_TIMEOUT,
                )
            except requests.RequestException as e:
                log.warning("paypal start %s failed: %s", start, str(e)[:100])
                break
            if response.status_code == 429 and attempt < 3:
                time.sleep(12 * attempt)
                continue
            try:
                payload = response.json()
            except ValueError:
                log.warning("paypal start %s HTTP %s", start, response.status_code)
                payload = None
            break
        if not payload:
            break
        data = payload.get("data") or {}
        total = int(data.get("count") or 0)
        positions = data.get("positions") or []
        fresh = [job for job in positions if str(job.get("id") or "") not in seen]
        if not fresh:
            break
        for job in fresh:
            ident = str(job.get("id") or "")
            seen.add(ident)
            when = job.get("postedTs")
            posted = None
            if isinstance(when, (int, float)) and when > 0:
                posted = datetime.fromtimestamp(when, timezone.utc).date().isoformat() + "T12:00:00+00:00"
            url = job.get("positionUrl") or f"https://paypal.eightfold.ai/careers/job/{ident}"
            if url.startswith("/"):
                url = "https://paypal.eightfold.ai" + url
            rows.append({
                "id": ident,
                "title": job.get("name") or "",
                "locs": paypal_places(job.get("standardizedLocations") or job.get("locations")),
                "posted": posted,
                "url": url,
            })
        start += len(positions)
        time.sleep(0.25)
    log.info("paypal: %d US listings", len(rows))

    def desc_of(row):
        try:
            payload = requests.get(
                "https://paypal.eightfold.ai/api/pcsx/position_details",
                params={"domain": "paypal.com", "position_id": row["id"]},
                headers=headers,
                timeout=_TIMEOUT,
            ).json()
        except (requests.RequestException, ValueError):
            return ""
        return ((payload.get("data") or {}).get("jobDescription")) or ""

    return _keep_rows("paypal", rows, desc_of)


def _pcsx_one(text: str) -> list[str]:
    """One location string from an Eightfold careers page."""
    text = re.sub(r"\s+", " ", unescape(text or "")).strip()
    if not text:
        return []
    match = re.match(r"US-([A-Z]{2})-(.+?)(?:\s*\(\d+\))?$", text)
    if match:
        state, rest = match.group(1), match.group(2).strip()
        if re.search(r"remote", rest, re.I) or _state_code(rest.split("-")[-1]) == state:
            return [f"Remote, {state}"]
        return [f"{rest.split('-')[-1].strip()}, {state}"]
    match = re.match(r"All Cities,\s*(.+?),\s*United States", text, re.I)
    if match and _state_code(match.group(1)):
        return [f"Remote, {_state_code(match.group(1))}"]
    match = re.match(r"AMER - United States - ([^-]+) - (.+)$", text, re.I)
    if match and _state_code(match.group(1).strip()):
        code = _state_code(match.group(1).strip())
        if re.search(r"remote", match.group(2), re.I):
            return [f"Remote, {code}"]
        return [f"{match.group(2).split(' - ')[0].strip()}, {code}"]
    match = re.match(r"(.+),\s*USA\s*-\s*Remote", text, re.I)
    if match and _state_code(match.group(1)):
        return [f"Remote, {_state_code(match.group(1))}"]
    match = re.match(r"(.+),\s*([A-Z]{2}),\s*USA$", text)
    if match:
        return [f"{match.group(1).strip()}, {match.group(2)}"]
    match = re.match(r"(.+),\s*(.+?),\s*United States", text, re.I)
    if match and _state_code(match.group(2)) and not re.search(r"all cities|remote", match.group(1), re.I):
        return [f"{match.group(1).strip()}, {_state_code(match.group(2))}"]
    if re.search(r"remote", text, re.I) and re.search(r"united states", text, re.I):
        head = re.split(r"\s+-\s+", text)[0]
        code = _state_code(head) or _state_code(head.split()[0])
        if code:
            return [f"Remote, {code}"]
    match = re.match(r"([^,]+),\s*([A-Z]{2})$", text)
    if match and _state_code(match.group(2)):
        return [f"{match.group(1).strip()}, {match.group(2)}"]
    return []


def pcsx_places(standardized: list[str] | None, locations: list[str] | None = None) -> list[str]:
    """Prefer 'Denver, CO, US'. State-only codes fall through to the written location."""
    out = []
    for loc in standardized or []:
        match = _PAYPAL_CITY.match(re.sub(r"\s+", " ", loc or "").strip())
        if not match:
            continue
        city, state = match.group(1).strip(), match.group(2)
        if city.lower() in {"washington dc", "washington d.c."}:
            city = "Washington"
        place = f"Remote, {state}" if city.lower() == "remote" else f"{city}, {state}"
        if place not in out:
            out.append(place)
    if out:
        return out
    for loc in locations or []:
        for place in _pcsx_one(loc):
            if place not in out:
                out.append(place)
    blob = " ".join(locations or [])
    if out:
        return out
    if locations and not re.search(r"united states|\bUSA\b|\bUS-", blob, re.I):
        return []
    return ["United States"]


def fetch_pcsx(which: str, origin: str, domain: str, start: int = 0) -> list[dict]:
    """Public Eightfold search. The query is already limited to the United States."""
    origin = origin.rstrip("/")
    headers = {**_BROWSER, "Referer": origin + "/careers"}
    rows, seen = [], set()
    total = start + 1
    while start < total and start < 5000:
        payload = None
        for attempt in (1, 2, 3):
            try:
                response = requests.get(
                    origin + "/api/pcsx/search",
                    params={"domain": domain, "query": "", "location": "United States", "start": start, "num": 20},
                    headers=headers,
                    timeout=_TIMEOUT,
                )
            except requests.RequestException as e:
                log.warning("%s start %s failed: %s", which, start, str(e)[:100])
                break
            if response.status_code == 429 and attempt < 3:
                time.sleep(12 * attempt)
                continue
            try:
                payload = response.json()
            except ValueError:
                log.warning("%s start %s HTTP %s", which, start, response.status_code)
                payload = None
            break
        if not payload:
            break
        data = payload.get("data") or {}
        total = int(data.get("count") or 0)
        positions = data.get("positions") or []
        fresh = [job for job in positions if str(job.get("id") or "") not in seen]
        if not fresh:
            break
        for job in fresh:
            ident = str(job.get("id") or "")
            seen.add(ident)
            when = job.get("postedTs")
            posted = None
            if isinstance(when, (int, float)) and when > 0:
                posted = datetime.fromtimestamp(when, timezone.utc).date().isoformat() + "T12:00:00+00:00"
            url = job.get("positionUrl") or f"{origin}/careers/job/{ident}"
            if url.startswith("/"):
                url = origin + url
            rows.append({
                "id": ident,
                "title": job.get("name") or "",
                "locs": pcsx_places(job.get("standardizedLocations"), job.get("locations")),
                "posted": posted,
                "url": url,
            })
        start += len(positions)
        time.sleep(0.35)
    log.info("%s: %d US listings", which, len(rows))

    def desc_of(row):
        for attempt in (1, 2):
            time.sleep(0.25)
            try:
                response = requests.get(
                    origin + "/api/pcsx/position_details",
                    params={"domain": domain, "position_id": row["id"]},
                    headers=headers,
                    timeout=_TIMEOUT,
                )
            except requests.RequestException:
                return ""
            if response.status_code == 429 and attempt == 1:
                time.sleep(6)
                continue
            try:
                payload = response.json()
            except ValueError:
                return ""
            return ((payload.get("data") or {}).get("jobDescription")) or ""
        return ""

    return _keep_rows(which, rows, desc_of)


def radancy_places(place: str) -> list[str]:
    """'Mountain View, California' -> 'Mountain View, CA'. Several cities are separated by semicolons."""
    text = re.sub(r"\s+", " ", unescape(place or "")).strip()
    if not text:
        return []
    out = []
    for piece in re.split(r";", text):
        piece = piece.strip(" ,")
        if not piece:
            continue
        if re.search(r"ireland|india|canada|united kingdom|singapore|israel|china|japan|germany|netherlands|taiwan", piece, re.I) and not re.search(r"united states", piece, re.I):
            continue
        parts = [part.strip() for part in piece.split(",") if part.strip()]
        if parts and re.search(r"united states|\busa\b", parts[-1], re.I):
            parts = parts[:-1]
        if len(parts) >= 2 and _state_code(parts[-1]):
            place = f"{parts[-2]}, {_state_code(parts[-1])}"
            if place not in out:
                out.append(place)
            continue
    return out


def radancy_rows(html: str, origin: str) -> list[dict]:
    out, seen = [], set()
    for match in re.finditer(r'href="([^"]*?/job/[^"]+/(\d+))"', html):
        ident = match.group(2)
        if ident in seen:
            continue
        window = html[match.start(): match.start() + 1200]
        title = re.search(r"<h[23]>(.*?)</h[23]>", window, re.S)
        loc = re.search(r'class="[^"]*location[^"]*">(.*?)</span>', window, re.S)
        if not title:
            continue
        seen.add(ident)
        out.append({
            "id": ident,
            "title": unescape(re.sub(r"<[^>]+>", "", title.group(1))),
            "locs": radancy_places(loc.group(1) if loc else ""),
            "posted": None,
            "url": origin.rstrip("/") + match.group(1),
        })
    return out


def _fetch_radancy(which: str, origin: str, queries: tuple[str, ...] = (), include_all: bool = True) -> list[dict]:
    """Talentbrew search used by Intuit and Synopsys. Location=United States is their country filter.

    An empty keyword walks the list until the page cap. Extra keywords catch roles past that cap.
    """
    headers = {**_BROWSER, "Referer": origin}
    rows, seen = [], set()
    terms = (("",) if include_all else ()) + tuple(queries)
    for query in terms or ("",):
        page, pages = 1, 1
        while page <= pages and page <= 80:
            found_rows = _radancy_page(which, origin, headers, query, page)
            if found_rows is None:
                break
            html, page_count = found_rows
            if page_count:
                pages = page_count
            fresh = [row for row in radancy_rows(html, origin) if row["id"] not in seen]
            if not fresh:
                break
            for row in fresh:
                seen.add(row["id"])
                rows.append(row)
            page += 1
            time.sleep(0.15)
    log.info("%s: %d listings", which, len(rows))

    def desc_of(row):
        try:
            html = requests.get(row["url"], headers=headers, timeout=_TIMEOUT).text
        except requests.RequestException:
            return ""
        spot = html.lower().find("job-description")
        return html[spot: spot + 12000] if spot >= 0 else ""

    return _keep_rows(which, rows, desc_of)


def _radancy_page(which: str, origin: str, headers: dict, query: str, page: int):
    try:
        payload = requests.get(
            origin.rstrip("/") + "/search-jobs/results",
            params={
                "ActiveFacetID": 0, "CurrentPage": page, "RecordsPerPage": 50,
                "Distance": 50, "RadiusUnitType": 0, "Keywords": query, "Location": "United States",
                "ShowRadius": "False", "IsPagination": "False", "CustomFacetName": "",
                "FacetTerm": "", "FacetType": 0, "SearchResultsModuleName": "Search Results",
                "SearchFiltersModuleName": "Search Filters", "SortCriteria": 0,
                "SortDirection": 0, "SearchType": 5, "ResultsType": 0,
            },
            headers=headers,
            timeout=_TIMEOUT,
        ).json()
    except (requests.RequestException, ValueError) as e:
        log.warning("%s %s page %s failed: %s", which, query or "all", page, str(e)[:100])
        return None
    html = payload.get("results") or ""
    found = re.search(r'data-total-pages="(\d+)"', html)
    return html, int(found.group(1)) if found else 0


def fetch_intuit() -> list[dict]:
    return _fetch_radancy("intuit", "https://jobs.intuit.com")


def fetch_synopsys() -> list[dict]:
    return _fetch_radancy("synopsys", "https://careers.synopsys.com")


def fetch_netapp() -> list[dict]:
    return _fetch_radancy("netapp", "https://careers.netapp.com")


def amd_places(job: dict) -> list[str]:
    """'Santa Clara' + 'California' -> 'Santa Clara, CA'. Other countries contribute nothing."""
    country = job.get("country") or ""
    if country and not re.search(r"united states|\busa\b|\bu\.s\b", country, re.I):
        return []
    city = re.sub(r"\s+", " ", job.get("city") or "").strip()
    code = _state_code(job.get("state") or "")
    if re.search(r"\bremote\b", f"{city} {job.get('location_name') or ''}", re.I) or re.match(r"home\b", city, re.I):
        return [f"Remote, {STATES[code]}"] if code in STATES else ["Remote"]
    if city and code:
        return [f"{city}, {code}"]
    parts = [part.strip() for part in (job.get("location_name") or "").split(",") if part.strip()]
    if len(parts) >= 3 and parts[0].upper() in {"US", "USA"}:
        code = _state_code(parts[1]) or (parts[1].upper() if len(parts[1]) == 2 else None)
        if re.match(r"home\b", parts[2], re.I):
            return [f"Remote, {STATES[code]}"] if code in STATES else ["Remote"]
        if code and parts[2]:
            return [f"{parts[2]}, {code}"]
    return [f"{city}, {code}"] if city and code else []


def amd_pay(job: dict) -> str:
    """The first salary pair on the listing, as '$150,500 - $215,000'."""
    def money(tag) -> str:
        raw = tag[0] if isinstance(tag, list) and tag else tag
        found = re.search(r"(\d{1,3}(?:,\d{3})+)", str(raw or ""))
        return found.group(1) if found else ""

    lo, hi = money(job.get("tags2")), money(job.get("tags3"))
    return f"${lo} - ${hi}" if lo and hi else ""


def fetch_amd() -> list[dict]:
    """careers.amd.com/careers-home/jobs loads GET /api/jobs. The description is already on each row."""
    headers = {**_BROWSER, "Referer": "https://careers.amd.com/careers-home/jobs"}
    rows, seen = [], set()
    page, total = 1, None
    while total is None or (page - 1) * 100 < total:
        try:
            payload = requests.get(
                "https://careers.amd.com/api/jobs",
                params={"page": page, "limit": 100, "country": "United States"},
                headers=headers,
                timeout=_TIMEOUT,
            ).json()
        except (requests.RequestException, ValueError) as e:
            log.warning("amd page %s failed: %s", page, str(e)[:100])
            break
        jobs = payload.get("jobs") or []
        if total is None:
            total = int(payload.get("totalCount") or 0)
        if not jobs:
            break
        for item in jobs:
            job = item.get("data") or {}
            ident = str(job.get("req_id") or job.get("slug") or "")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            posted = (job.get("posted_date") or "")[:10]
            rows.append({
                "id": ident,
                "title": job.get("title") or "",
                "locs": amd_places(job),
                "posted": posted + "T12:00:00+00:00" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", posted) else None,
                "url": f"https://careers.amd.com/careers-home/jobs/{ident}",
                "desc": strip_html(job.get("description") or "") + " " + amd_pay(job),
            })
        page += 1
        if page > 20:
            break
        time.sleep(0.2)
    log.info("amd: %d US listings", len(rows))
    return _keep_rows("amd", rows)


def supermicro_places(text: str) -> list[str]:
    """'United States - San Jose, California' -> 'San Jose, CA'. Other countries contribute nothing."""
    text = re.sub(r"\s+", " ", unescape(text or "")).strip(" -")
    if " - " in text:
        country, rest = text.split(" - ", 1)
        if not re.search(r"united states|\busa\b|\bu\.s\b", country, re.I):
            return []
        text = rest.strip()
    if re.search(r"netherlands|taiwan|india|china|japan|singapore|germany|israel|korea|malaysia", text, re.I):
        return []
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) >= 3 and re.fullmatch(r"[A-Za-z]{2}", parts[-1]) and parts[-1].upper() != "US":
        return []
    if len(parts) >= 3 and re.search(r"united states|\busa\b", parts[-1], re.I):
        code = _state_code(parts[-2])
        if code:
            return [f"{parts[-3]}, {code}"]
    if len(parts) == 2:
        code = _state_code(parts[-1])
        if code:
            return [f"{parts[0]}, {code}"]
    return [text] if text else []


def supermicro_rows(html: str, origin: str = "https://jobs.supermicro.com") -> list[dict]:
    out, seen = [], set()
    for match in re.finditer(r'href="(/job/[^"]*?/(\d+)/)"', html):
        ident = match.group(2)
        if ident in seen:
            continue
        window = html[match.start(): match.start() + 1800]
        title = re.search(r">([^<]+)</a>", window)
        loc = re.search(r'class="jobLocation">\s*([^<]+?)\s*<', window)
        if not title or not loc:
            continue
        places = supermicro_places(loc.group(1))
        seen.add(ident)
        if not places:
            continue
        out.append({
            "id": ident,
            "title": unescape(title.group(1)),
            "locs": places,
            "posted": None,
            "url": origin.rstrip("/") + match.group(1),
        })
    return out


def _fetch_successfactors(which: str, origin: str) -> list[dict]:
    """SuccessFactors search pages. The location is on the row; other countries are dropped."""
    origin = origin.rstrip("/")
    headers = {**_BROWSER, "Referer": origin + "/search/"}
    rows, seen = [], set()
    start = 0
    while start < 4000:
        try:
            html = requests.get(
                origin + "/search/",
                params={"q": "", "startrow": start, "sortColumn": "referencedate", "sortDirection": "desc"},
                headers=headers,
                timeout=_TIMEOUT,
            ).text
        except requests.RequestException as e:
            log.warning("%s start %s failed: %s", which, start, str(e)[:100])
            break
        for row in supermicro_rows(html, origin):
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)
        total = re.search(r"of <b>(\d+)</b>", html)
        start += 25
        if not total or start >= int(total.group(1)):
            break
        time.sleep(0.1)
    log.info("%s: %d US listings", which, len(rows))

    def desc_of(row):
        try:
            html = requests.get(row["url"], headers=headers, timeout=_TIMEOUT).text
        except requests.RequestException:
            return ""
        spot = html.lower().find("jobdescription")
        return html[spot: spot + 12000] if spot >= 0 else ""

    return _keep_rows(which, rows, desc_of)


def fetch_supermicro() -> list[dict]:
    return _fetch_successfactors("supermicro", "https://jobs.supermicro.com")


def anduril_places(locs) -> list[str]:
    """'Quincy, Massachusetts, United States' -> 'Quincy, MA'."""
    if isinstance(locs, str):
        locs = [locs]
    out = []
    for loc in locs or []:
        text = re.sub(r"\s+", " ", unescape(str(loc or ""))).strip()
        if not re.search(r"united states", text, re.I):
            continue
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) >= 3:
            code = _state_code(parts[-2])
            if code:
                place = f"{parts[-3]}, {code}"
                if place not in out:
                    out.append(place)
                continue
        if text not in out:
            out.append(text)
    return out


def fetch_anduril() -> list[dict]:
    """The open-roles page searches a public index. Apply links are Greenhouse."""
    headers = {**_BROWSER, "Accept": "text/html", "Referer": "https://www.anduril.com/open-roles"}
    try:
        page = requests.get("https://www.anduril.com/open-roles", headers=headers, timeout=_TIMEOUT).text
        src = re.search(r'src="(assets/js/app\.[^"]+\.js)"', page)
        js = requests.get("https://www.anduril.com/" + src.group(1), headers=headers, timeout=_TIMEOUT).text if src else ""
        creds = re.search(r'searchClient\("([A-Z0-9]+)","([a-f0-9]+)"\)', js)
    except requests.RequestException as e:
        log.warning("anduril setup failed: %s", str(e)[:100])
        return []
    if not creds:
        log.warning("anduril search client was not on the page")
        return []
    app, key = creds.group(1), creds.group(2)
    api = {"X-Algolia-Application-Id": app, "X-Algolia-API-Key": key, "Content-Type": "application/json"}

    def query(body: dict) -> dict:
        return requests.post(
            f"https://{app}-dsn.algolia.net/1/indexes/jobs/query",
            headers=api,
            json=body,
            timeout=_TIMEOUT,
        ).json()

    try:
        facets = query({"query": "", "hitsPerPage": 0, "facets": ["location"], "maxValuesPerFacet": 100})
    except (requests.RequestException, ValueError) as e:
        log.warning("anduril locations failed: %s", str(e)[:100])
        return []
    places = [
        name for name in ((facets.get("facets") or {}).get("location") or {})
        if re.search(r"united states", name, re.I)
    ]
    # An unfiltered search stops at 2,000, so each US office is read on its own and the two passes collapse by id.
    places = [None, *places]
    rows, seen = [], set()
    for place in places:
        page_no = 0
        while page_no < 25:
            try:
                body = {"query": "", "hitsPerPage": 100, "page": page_no}
                if place:
                    body["facetFilters"] = [f"location:{place}"]
                data = query(body)
            except (requests.RequestException, ValueError) as e:
                log.warning("anduril %s page %s failed: %s", place, page_no, str(e)[:80])
                break
            hits = data.get("hits") or []
            if not hits:
                break
            for hit in hits:
                ident = str(hit.get("requisitionId") or hit.get("objectID") or "")
                if not ident or ident in seen:
                    continue
                seen.add(ident)
                rows.append({
                    "id": ident,
                    "title": hit.get("title") or "",
                    "locs": anduril_places(hit.get("location")),
                    "posted": None,
                    "url": hit.get("url") or f"https://boards.greenhouse.io/andurilindustries/jobs/{ident}",
                    "desc": hit.get("content") or "",
                })
            page_no += 1
            if page_no >= int(data.get("nbPages") or 0):
                break
            time.sleep(0.05)
    log.info("anduril: %d listings", len(rows))
    return _keep_rows("anduril", rows)


def fetch_all() -> list[dict]:
    raws = []
    for name, fn in (
        ("apple", fetch_apple),
        ("amazon", fetch_amazon),
        ("google", fetch_google),
        ("netflix", fetch_netflix),
        ("meta", fetch_meta),
        ("tesla", fetch_tesla),
        ("microsoft", fetch_microsoft),
        ("salesforce", fetch_salesforce),
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
    return raws
