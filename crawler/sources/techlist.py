"""Build the tech-track company list.

Two sources, United States companies only:

- Public: S&P 500 companies in Information Technology, plus the internet
  platforms the index files under another sector (Amazon, Alphabet, Meta,
  Netflix, Uber, and the other tickers in PLATFORM_TICKERS). A legal
  headquarters abroad does not drop a US company (Accenture, NXP, Seagate,
  TE Connectivity).
- Unicorns: CB Insights companies valued at $5B or more in enterprise tech,
  fintech, or media. Hardware and defense unicorns qualify at $10B.
  Chinese companies are left out even when a row is tagged United States.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

_QID = re.compile(r"^Q\d+$")
_DROP_NAME = re.compile(
    r"\b(university|college|records|pictures|museum|library|foundation|broadcasting|"
    r"television|newspaper|magazine|philharmonic|orchestra|entertainment)\b",
    re.I,
)
_DROP_HOSTS = {
    "wikipedia.org", "wikimedia.org", "wikidata.org", "facebook.com", "linkedin.com",
    "twitter.com", "x.com", "youtube.com", "instagram.com", "medium.com", "t.me",
    "linktr.ee", "bit.ly", "github.io",
}


def _host(url: str) -> str:
    if "://" not in (url or ""):
        url = "https://" + (url or "")
    host = urlparse(url).netloc.lower().split(":")[0].removeprefix("www.")
    return host[4:] if host.startswith("www.") else host


def _blocked(host: str) -> bool:
    if not host or host.endswith((".edu", ".gov", ".mil")):
        return True
    return any(host == d or host.endswith("." + d) for d in _DROP_HOSTS)


def _prefer(new: str, old: str) -> bool:
    """When two sites share a name, keep the shorter .com."""
    def score(d: str) -> tuple:
        return (d.endswith(".com"), d.endswith(".ai"), d.endswith(".io"), -len(d))
    return score(new) > score(old)


# GICS puts these under retail, autos, ads, or payments. They are US tech platforms.
PLATFORM_TICKERS = frozenset({
    "AMZN", "TSLA", "UBER", "ABNB", "DASH", "CART", "COIN", "NFLX",
    "APP", "TTD", "XYZ", "PYPL", "VEEV",
})
PLATFORM_SUB = frozenset({
    "Interactive Media & Services",
    "Interactive Home Entertainment",
})

UNICORN_INDUSTRIES = frozenset({"Enterprise Tech", "Financial Services", "Media & Entertainment"})
_CHINA_NAME = re.compile(r"tiktok|bytedance|shein|alibaba|tencent|baidu|huawei|xiaomi|didi", re.I)
# Quince is a clothing brand. The unicorn table files it under enterprise tech.
_NOT_TECH = re.compile(r"\bquince\b", re.I)
_INDUSTRIAL_SKIP = re.compile(r"boring company|artin energy|huolala|base power|zongmu", re.I)


def select_public(rows: list[dict]) -> list[dict]:
    """S&P 500 rows with Symbol, Security, GICS Sector, GICS Sub-Industry, Headquarters Location.

    Headquarters outside the United States does not drop the company. The S&P
    technology names are US companies even when the legal seat is Ireland or
    the Netherlands.
    """
    seen: set[str] = set()
    out = []
    for row in rows:
        ticker = (row.get("Symbol") or "").strip()
        if not ticker:
            continue
        sector = row.get("GICS Sector") or ""
        sub = row.get("GICS Sub-Industry") or ""
        if sector != "Information Technology" and sub not in PLATFORM_SUB and ticker not in PLATFORM_TICKERS:
            continue
        name = re.sub(r"\s*\(Class [A-Z]\)\s*$", "", (row.get("Security") or "").strip())
        key = re.sub(r"[^a-z0-9]+", "", name.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({
            "name": name,
            "ticker": ticker,
            "kind": "public",
            "sector": sector,
            "hq": row.get("Headquarters Location") or "",
        })
    return out


def select_unicorns(rows: list[dict], floor: float = 5, industrial_floor: float = 10) -> list[dict]:
    """Rows with name, valuation (USD billions), country, industry. US only, no Chinese firms."""
    out = []
    seen: set[str] = set()
    for row in rows:
        name = (row.get("name") or "").strip()
        country = (row.get("country") or "").strip()
        industry = (row.get("industry") or "").strip()
        try:
            valuation = float(row.get("valuation") or 0)
        except (TypeError, ValueError):
            continue
        if country != "United States" or not name or _CHINA_NAME.search(name) or _NOT_TECH.search(name):
            continue
        key = re.sub(r"[^a-z0-9]+", "", name.lower())
        if key in seen:
            continue
        keep = industry in UNICORN_INDUSTRIES and valuation >= floor
        if industry in {"Industrials", "Industrial"} and valuation >= industrial_floor and not _INDUSTRIAL_SKIP.search(name):
            keep = True
        if not keep:
            continue
        seen.add(key)
        out.append({
            "name": name,
            "kind": "unicorn",
            "valuation": valuation,
            "industry": industry,
            "country": "United States",
        })
    out.sort(key=lambda c: (-c["valuation"], c["name"].lower()))
    return out


def clean_wikidata(rows: list[dict], limit: int = 1000) -> list[dict]:
    by_name: dict[str, dict] = {}
    for row in rows:
        label = (row.get("companyLabel") or {}).get("value", "").strip()
        site = (row.get("website") or {}).get("value", "").strip()
        host = _host(site)
        if not host or _blocked(host):
            continue
        if _QID.match(label):
            label = host.split(".")[0].replace("-", " ").title()
        if not label or _DROP_NAME.search(label):
            continue
        try:
            sitelinks = int((row.get("sitelinks") or {}).get("value") or 0)
        except ValueError:
            sitelinks = 0
        employees = None
        if "employees" in row:
            try:
                employees = int(float(row["employees"]["value"])) or None
            except (ValueError, KeyError):
                employees = None
        cand = {
            "name": label,
            "domain": host,
            "website": f"https://{host}",
            "sitelinks": sitelinks,
            "employees": employees,
        }
        key = re.sub(r"[^a-z0-9]+", "", label.lower())
        cur = by_name.get(key)
        if not cur or sitelinks > cur["sitelinks"] or (sitelinks == cur["sitelinks"] and _prefer(host, cur["domain"])):
            by_name[key] = cand

    by_dom: dict[str, dict] = {}
    for cand in by_name.values():
        cur = by_dom.get(cand["domain"])
        if not cur or cand["sitelinks"] > cur["sitelinks"]:
            by_dom[cand["domain"]] = cand
    ranked = sorted(by_dom.values(), key=lambda c: (-c["sitelinks"], -(c["employees"] or 0), c["name"].lower()))
    return ranked[:limit]
