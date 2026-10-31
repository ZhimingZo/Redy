"""Rule-based classifiers: seniority level, role family, sub-family, US location, pay, YOE.

Every function is pure and covered by tests/test_classify.py. An LLM fallback for
ambiguous titles can be added later behind `level == "unk"`.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- seniority

LEVELS = ("junior", "mid", "senior", "staff", "mgmt", "unk")
LEVEL_RANK = {"junior": 0, "unk": 1, "mid": 2, "senior": 3, "staff": 4, "mgmt": 4}

_MGMT = re.compile(
    r"\b(engineering manager|manager,? (software|engineering|ml|machine learning|data|infrastructure|platform|security|applied)"
    r"|(software|ml|data|infrastructure|platform|security|research) (engineering )?manager|eng(ineering)? mgr"
    r"|head of|director|\bvp\b|vice president|\bcto\b|chief [a-z ]*officer|\bcpo\b"
    r"|group product manager|\bgpm\b|senior manager|product director|tech lead manager|\btlm\b)",
    re.I,
)
_STAFF = re.compile(r"\b(staff|principal|distinguished|fellow)\b", re.I)
_JUNIOR = re.compile(
    r"\b(intern(ship)?|new[- ]?grad(uate)?|graduate|junior|jr\.?|entry[- ]level|early[- ]career|apprentice|co-?op|student"
    r"|associate (software|product|engineer|data|ml|machine)|associate pm|\bapm\b)\b"
    r"|\b(engineer|developer|scientist|manager)\s*(i|ii|1|2)\b(?!\s*[-/]?\s*(iii|iv|3|4))",
    re.I,
)
_SENIOR = re.compile(r"\b(senior|sr\.?|lead|founding|iii|iv|architect)\b|\b(engineer|developer|scientist)\s*(3|4|5)\b", re.I)


def level_from_title(title: str) -> str | None:
    # "Member of Technical Staff" is used for every level (e.g. OpenAI), so it carries no level signal
    t = re.sub(r"member of (the )?technical staff|\bmts\b", "", title or "", flags=re.I)
    if _MGMT.search(t):
        return "mgmt"
    if _STAFF.search(t):
        return "staff"
    if _JUNIOR.search(t):
        return "junior"
    if _SENIOR.search(t):
        return "senior"
    return None


def level_from_yoe(yoe: int | None) -> str:
    if yoe is None:
        return "unk"
    if yoe >= 5:
        return "senior"
    if yoe >= 3:
        return "mid"
    return "junior"


def classify_level(title: str, yoe: int | None, new_grads_ok: bool = False) -> str:
    """Title signals win; otherwise fall back to minimum years of experience."""
    by_title = level_from_title(title)
    if by_title:
        return by_title
    if new_grads_ok:
        return "junior"
    return level_from_yoe(yoe)


# ---------------------------------------------------------------- years of experience

_YOE = re.compile(
    r"(?:(?:at least|minimum(?: of)?|min\.?)\s+)?(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?\s*)?(?:years|yrs)",
    re.I,
)
_YOE_CONTEXT = re.compile(r"experience|exp\b|industry|professional|working|building|shipping|engineering|product|software", re.I)
_YOE_SKIP = re.compile(r"less than|within the last|since|old|ago|clearance|vesting|anniversary", re.I)


def parse_yoe(snippets: list[str] | None) -> int | None:
    """Minimum years required, taken as the largest 'N+ years of experience' found."""
    best = None
    for s in snippets or []:
        if _YOE_SKIP.search(s) or not _YOE_CONTEXT.search(s):
            continue
        for m in _YOE.finditer(s):
            n = int(m.group(1))
            if 0 < n <= 20:
                best = n if best is None else max(best, n)
    return best


def parse_yc_experience(exp: str | None) -> tuple[int | None, bool]:
    """'6+ years' -> (6, False); 'Any (new grads ok)' -> (0, True)."""
    if not exp:
        return None, False
    if "new grad" in exp.lower():
        return 0, True
    m = re.search(r"(\d+)", exp)
    return (int(m.group(1)), False) if m else (None, False)


# ---------------------------------------------------------------- role family

_PM = re.compile(
    r"\b(product manager|product management|product lead|head of product|director,? (of )?product|vp,? (of )?product"
    r"|chief product officer|\bcpo\b|group pm|product owner|\bpm\b|product,? (growth|platform|ai)\b(?! (engineer|designer|marketing)))",
    re.I,
)
_PM_EXCLUDE = re.compile(r"product (marketing|design|designer|analyst|operations|ops|support|specialist|engineer)", re.I)
_ENG = re.compile(
    r"engineer|developer|\bsre\b|devops|architect|scientist|\bcto\b|programmer|\bswe\b|technical staff|\bmts\b"
    r"|head of (engineering|ai|ml|infrastructure|platform|research)|vp,? (of )?engineering|researcher",
    re.I,
)
_ENG_EXCLUDE = re.compile(
    r"sales engineer|solutions? (engineer|architect|consultant)|support engineer|customer (success |support )?engineer"
    r"|field (application|service)s? engineer|implementation (engineer|specialist)|value engineer|pre-?sales"
    r"|recruit|talent|sourcer|marketing|account (executive|manager)|\bsales\b|\bgtm\b|legal|counsel|finance|accountant"
    r"|technician|mechanic|operator|strategist|data analyst|business analyst|program manager|project manager|test pilot"
    r"|it support|help ?desk|clinical|nurse",
    re.I,
)


def classify_family(title: str, hint: str | None = None) -> str | None:
    """'eng', 'pm' or None (out of scope)."""
    t = title or ""
    if _PM.search(t) and not _PM_EXCLUDE.search(t):
        return "pm"
    if _ENG.search(t) and not _ENG_EXCLUDE.search(t):
        return "eng"
    if hint == "product" and not _PM_EXCLUDE.search(t) and not _ENG_EXCLUDE.search(t) and re.search(r"product", t, re.I):
        return "pm"
    return None


_SUBFAMILIES = [
    ("Eng Management", r"engineering manager|manager,? (software|engineering)|head of (engineering|ai|ml|infrastructure|platform)|director|\bvp\b|\bcto\b|tech lead manager"),
    ("AI / ML", r"machine learning|\bml\b|\bai\b|llm|deep learning|computer vision|perception|\bnlp\b|research (scientist|engineer)|applied scientist|inference|model|researcher|autonomy"),
    ("Data", r"data (engineer|scientist|platform|infrastructure)|analytics engineer"),
    ("Security", r"security|offensive|appsec|detection|red team"),
    ("Infra / Platform", r"infrastructure|platform|devops|\bsre\b|reliability|cloud|distributed|kernel|network|storage|compiler|gpu|performance|systems engineer|site reliability|developer productivity|devex|build"),
    ("Mobile", r"\bios\b|android|mobile"),
    ("Frontend", r"front.?end|\bui\b|web engineer|design engineer"),
    ("Hardware / Robotics", r"hardware|mechanical|electrical|firmware|embedded|robotic|avionics|propulsion|manufactur|aero|spacecraft|satellite|flight|\bgnc\b|mechatronic|optical|photonic|battery|vehicle|\brf\b|asic|fpga|structur|test engineer|gnc|controls|thermal|power electronics|integration engineer|reliability engineer|quality engineer|materials"),
    ("Backend / Full-stack", r"back.?end|full.?stack|software|\bapi\b|product engineer|forward deployed|founding engineer|member of technical staff|\bswe\b"),
]


def classify_subfamily(title: str, family: str) -> str:
    if family == "pm":
        return "Product"
    for name, pat in _SUBFAMILIES:
        if re.search(pat, title or "", re.I):
            return name
    return "Other Eng"


# ---------------------------------------------------------------- US locations

_STATES = "AL|AK|AZ|AR|CA|CO|CT|DC|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY"
_US = re.compile(
    rf"united states|\busa\b|\bu\.s\.|,\s*us\b|\(us\)|\bus\b(?=[\s,)-]|$)|,\s*({_STATES})\b"
    r"|san francisco|\bsf\b|bay area|new york|\bnyc\b|seattle|austin|boston|los angeles|palo alto|mountain view|menlo park"
    r"|redwood city|san mateo|sunnyvale|san jose|oakland|berkeley|el segundo|costa mesa|long beach|torrance|hawthorne|irvine|san diego"
    r"|denver|boulder|chicago|washington|arlington|atlanta|miami|brooklyn|pittsburgh|philadelphia|salt lake|houston|dallas"
    r"|nashville|portland|minneapolis|detroit|phoenix|raleigh|durham",
    re.I,
)
_NON_US = re.compile(
    r"canada|toronto|vancouver|montreal|\buk\b|united kingdom|london|england|europe|emea|\bindia\b|bangalore|bengaluru|germany|berlin"
    r"|munich|paris|france|mexico|brazil|singapore|japan|tokyo|australia|sydney|israel|tel aviv|ireland|dublin|netherlands"
    r"|amsterdam|spain|madrid|barcelona|poland|warsaw|switzerland|zurich|latam|apac|korea|seoul|china|taiwan|argentina|colombia"
    r"|portugal|lisbon|italy|sweden|stockholm|denmark|finland|norway|philippines|vietnam|nigeria|kenya|uae|dubai",
    re.I,
)


def us_locations(locs: list[str] | None, country: str | None = None) -> list[str]:
    """Return the US (or US-remote) locations from a job's location list."""
    out = []
    for raw in locs or []:
        for loc in re.split(r"\s+/\s+|;|\s\|\s", raw or ""):
            loc = loc.strip()
            if not loc:
                continue
            parts = [p.strip() for p in loc.split(",")]
            # "City, Region, CC": the last 2-letter code is a country, so ", IN" there means India, not Indiana
            if len(parts) >= 3 and re.fullmatch(r"[A-Za-z]{2}", parts[-1]) and parts[-1].upper() != "US":
                continue
            if _NON_US.search(loc) and not re.search(rf"united states|\busa\b|\(us\)|,\s*us\b|,\s*({_STATES})\b", loc, re.I):
                continue
            if _US.search(loc):
                out.append(loc)
            elif re.search(r"remote|anywhere|distributed", loc, re.I) and not _NON_US.search(loc):
                if not country or re.search(r"^(us|usa|united states)", country, re.I):
                    out.append(loc)
    seen, uniq = set(), []
    for l in out:
        k = l.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(l)
    return uniq


def is_remote(locs: list[str], flag: bool | None = None) -> bool:
    return bool(flag) or any(re.search(r"remote|anywhere", l, re.I) for l in locs)


# ---------------------------------------------------------------- pay

def _money(s: str) -> float | None:
    s = s.replace(",", "").replace("$", "").strip()
    m = re.match(r"(\d+(?:\.\d+)?)\s*([kKmM]?)", s)
    if not m:
        return None
    v = float(m.group(1))
    return v * (1000 if m.group(2) in "kK" and m.group(2) else 1_000_000 if m.group(2) in "mM" and m.group(2) else 1)


def parse_yc_salary(s: str | None) -> list[int] | None:
    if not s:
        return None
    parts = [p for p in re.split(r"\s*[-–]\s*", s) if p.strip()]
    vals = [v for v in (_money(p) for p in parts) if v]
    if not vals:
        return None
    return normalize_pay(min(vals), max(vals), "USD", "year")


def normalize_pay(lo, hi, currency, interval) -> list[int] | None:
    """Annual USD [min, max] or None when not comparable."""
    if lo is None:
        return None
    if currency and str(currency).upper() not in ("USD", "$"):
        return None
    hi = hi or lo
    iv = str(interval or "year").lower()
    if "hour" in iv:
        lo, hi = lo * 2080, hi * 2080
    elif "month" in iv:
        lo, hi = lo * 12, hi * 12
    if not (30_000 <= lo <= 2_000_000 and lo <= hi <= 3_000_000):
        return None
    return [int(round(lo, -3)), int(round(hi, -3))]


# ---------------------------------------------------------------- dates

def days_from_ago(ago: str | None) -> int | None:
    """YC relative age ('about 2 months', 'over 1 year', '3 days') -> days."""
    if not ago:
        return None
    a = ago.lower()
    if re.search(r"minute|hour|second|today|just", a):
        return 0
    m = re.search(r"(\d+|a|an)\s+(day|week|month|year)", a)
    if not m:
        return None
    n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
    unit = {"day": 1, "week": 7, "month": 30, "year": 365}[m.group(2)]
    d = n * unit
    if a.startswith("over"):
        d = int(d * 1.15)
    elif a.startswith("almost"):
        d = int(d * 0.92)
    return d


# ---------------------------------------------------------------- stage

def stage_bucket(stage: str | None, backer: str) -> str:
    s = (stage or "").lower()
    if not s or s in ("venture", "null"):
        return "Unknown"
    if s in ("seed", "early", "accelerator", "pre-seed"):
        return "Seed / Early"
    if s in ("series a", "series b"):
        return "Series A–B"
    if s.startswith("series"):
        return "Series C+"
    if s == "growth":
        return "Growth"
    return "Unknown"


def short_batch(batch: str | None) -> str | None:
    if not batch:
        return None
    m = re.match(r"(winter|summer|fall|spring|x)\s*(\d{4})", batch, re.I)
    if not m:
        return batch
    return {"winter": "W", "summer": "S", "fall": "F", "spring": "X", "x": "X"}[m.group(1).lower()] + m.group(2)[2:]
