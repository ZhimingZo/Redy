"""Find the ATS behind a company's own website.

Greenhouse, Lever and Ashby can be crawled. Workday, SmartRecruiters, Workable,
iCIMS, Oracle and Eightfold are only recorded, so the next adapter knows which
official board to start from.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

_SKIP_SLUGS = {
    "embed", "jobs", "job", "blog", "legal", "privacy", "login", "signin",
    "candidates", "search", "www", "about", "careers",
}
CRAWLABLE = {"greenhouse", "lever", "ashby"}

_ATS = (
    ("greenhouse", re.compile(
        r"(?:https?://)?(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"(?:https?://)?jobs\.lever\.co/([A-Za-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"(?:https?://)?jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)", re.I)),
    ("workday", re.compile(r"(?:https?://)?([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)", re.I)),
    ("smartrecruiters", re.compile(r"(?:https?://)?(?:jobs\.)?smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"(?:https?://)?apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)),
    ("icims", re.compile(r"(?:https?://)?([a-z0-9-]+)\.icims\.com", re.I)),
    ("oracle", re.compile(r"(?:https?://)?([a-z0-9.-]+\.oraclecloud\.com)", re.I)),
    ("eightfold", re.compile(r"(?:https?://)?([a-z0-9.-]+\.eightfold\.ai)", re.I)),
)

_HREF = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.I)
_CAREER = re.compile(r"career|jobs|join-us|joinus|work-with-us|openings|vacancies", re.I)
_OFFSITE = ("linkedin.com", "glassdoor.com", "indeed.com", "wikipedia.org", "facebook.com", "twitter.com", "instagram.com", "youtube.com")
_STOP = {
    "inc", "llc", "ltd", "corp", "corporation", "company", "co", "group", "the", "plc",
    "sa", "ag", "nv", "holdings", "technologies", "technology", "labs", "lab", "international",
}


def classify_board(url: str, html: str = "") -> dict | None:
    blob = f"{url or ''}\n{(html or '')[:250000]}"
    # Workday embeds the host as \u002fflir.wd1...; without this the slug keeps the escape.
    blob = blob.replace("\\u002f", "/").replace("\\/", "/")
    for ats, rx in _ATS:
        m = rx.search(blob)
        if not m:
            continue
        slug = (m.group(1) or "").strip("/")
        if ats in CRAWLABLE:
            slug = slug.lower()
            if not slug or slug in _SKIP_SLUGS:
                continue
        return {"ats": ats, "slug": slug}
    return None


def career_links(html: str, base: str) -> list[str]:
    out = []
    for href in _HREF.findall(html or ""):
        if not _CAREER.search(href):
            continue
        absu = urljoin(base, href.strip())
        host = urlparse(absu).netloc.lower()
        if urlparse(absu).scheme not in ("http", "https"):
            continue
        if any(host == h or host.endswith("." + h) for h in _OFFSITE):
            continue
        out.append(absu.split("#")[0])
    return list(dict.fromkeys(out))[:4]


_GENERIC_LABELS = {"about", "www", "store", "jobs", "careers", "home", "blog", "shop", "global", "holdings", "group", "corp", "corporate"}
_COMMON_TLDS = {"com", "org", "net", "io", "ai", "co", "jp", "de", "cn", "uk", "fr", "in", "us", "tv", "me", "app", "dev", "xyz", "gg", "se", "nl", "kr", "br", "au", "ca", "it", "es", "pl", "il"}


def slug_from_domain(domain: str) -> str:
    labels = [p for p in (domain or "").lower().removeprefix("www.").split(".") if p]
    if not labels:
        return ""
    if len(labels) >= 3 and labels[-2] in {"co", "com", "ac", "org", "net", "gov"}:
        base = labels[-3]
    elif len(labels) >= 2:
        base = labels[-2]
    else:
        base = labels[0]
    # about.google and holdings.panasonic keep the brand, not the prefix
    if base in _GENERIC_LABELS and labels[-1] not in _COMMON_TLDS:
        return labels[-1]
    return base


def names_match(company: str, board: str, domain_slug: str = "") -> bool:
    def toks(s: str) -> set[str]:
        return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if t not in _STOP and len(t) > 1}

    board_compact = re.sub(r"[^a-z0-9]", "", (board or "").lower())
    if domain_slug and board_compact == domain_slug:
        return True
    words = [t for t in re.findall(r"[A-Za-z0-9]+", company or "") if t.lower() not in _STOP]
    initials = "".join(w[0] for w in words).lower()
    if len(initials) >= 2 and board_compact == initials:
        return True
    a, b = toks(company), toks(board)
    return bool(a and b and (a & b))
