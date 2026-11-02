"""Location cleanup: turn the many ways boards write a place into one display form.

    "San Francisco, California, United States"  -> "San Francisco, CA"
    "NYC HQ" / "New York City (Hybrid)"           -> "New York, NY"
    "US - Remote" / "United States (Remote)"      -> "Remote (US)"
    "Remote (IN)" / "Toronto, ON, CA"             -> dropped (not US)

`pretty_locations(raw_list)` returns (display_locations, is_remote). A job with no US location
left after cleanup is not a US job. Pure functions; covered by tests/test_locations.py.
"""
from __future__ import annotations

import re

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DC": "Washington, DC", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}
_STATE_BY_NAME = {v.lower(): k for k, v in STATES.items() if k != "DC"}
_STATE_BY_NAME.update({"district of columbia": "DC", "d.c.": "DC", "dc": "DC", "virgina": "VA", "calif": "CA", "cali": "CA"})

# bare city names (and nicknames) that boards write without a state
CITIES = {
    "san francisco": ("San Francisco", "CA"), "sf": ("San Francisco", "CA"), "san franciso": ("San Francisco", "CA"),
    "south san francisco": ("South San Francisco", "CA"),
    "new york": ("New York", "NY"), "new york city": ("New York", "NY"), "nyc": ("New York", "NY"), "ny": ("New York", "NY"),
    "manhattan": ("New York", "NY"), "brooklyn": ("Brooklyn", "NY"),
    "los angeles": ("Los Angeles", "CA"), "seattle": ("Seattle", "WA"), "bellevue": ("Bellevue", "WA"),
    "redmond": ("Redmond", "WA"), "kirkland": ("Kirkland", "WA"), "austin": ("Austin", "TX"), "houston": ("Houston", "TX"),
    "dallas": ("Dallas", "TX"), "boston": ("Boston", "MA"), "chicago": ("Chicago", "IL"), "denver": ("Denver", "CO"),
    "boulder": ("Boulder", "CO"), "palo alto": ("Palo Alto", "CA"), "mountain view": ("Mountain View", "CA"),
    "menlo park": ("Menlo Park", "CA"), "sunnyvale": ("Sunnyvale", "CA"), "san jose": ("San Jose", "CA"),
    "san mateo": ("San Mateo", "CA"), "redwood city": ("Redwood City", "CA"), "foster city": ("Foster City", "CA"),
    "oakland": ("Oakland", "CA"), "berkeley": ("Berkeley", "CA"), "santa clara": ("Santa Clara", "CA"),
    "cupertino": ("Cupertino", "CA"), "el segundo": ("El Segundo", "CA"), "el segudo": ("El Segundo", "CA"),
    "torrance": ("Torrance", "CA"), "hawthorne": ("Hawthorne", "CA"), "irvine": ("Irvine", "CA"),
    "costa mesa": ("Costa Mesa", "CA"), "long beach": ("Long Beach", "CA"), "santa monica": ("Santa Monica", "CA"),
    "culver city": ("Culver City", "CA"), "san diego": ("San Diego", "CA"), "atlanta": ("Atlanta", "GA"),
    "miami": ("Miami", "FL"), "pittsburgh": ("Pittsburgh", "PA"), "philadelphia": ("Philadelphia", "PA"),
    "washington": ("Washington", "DC"), "washington dc": ("Washington", "DC"), "washington d.c.": ("Washington", "DC"),
    "dc": ("Washington", "DC"), "d.c.": ("Washington", "DC"),
    "salt lake city": ("Salt Lake City", "UT"), "lehi": ("Lehi", "UT"), "phoenix": ("Phoenix", "AZ"),
    "nashville": ("Nashville", "TN"), "raleigh": ("Raleigh", "NC"), "durham": ("Durham", "NC"),
    "raleigh-durham": ("Raleigh", "NC"), "minneapolis": ("Minneapolis", "MN"), "detroit": ("Detroit", "MI"),
    "ann arbor": ("Ann Arbor", "MI"), "portland": ("Portland", "OR"), "boise": ("Boise", "ID"),
}
BAY_AREA = "SF Bay Area"
_BAY = re.compile(r"^((sf|san francisco) )?bay( area)?$", re.I)

_US_COUNTRY = re.compile(r"^(us|usa|u\.?s\.?a?\.?|united states( of america)?|america)$", re.I)
_GLOBAL = re.compile(r"^(global|anywhere|worldwide|americas|north america|us/global)$", re.I)
_NON_US = re.compile(
    r"canada|toronto|vancouver|montreal|ottawa|waterloo|calgary|\buk\b|united kingdom|london|england|europe|\beu\b|emea"
    r"|\bindia\b|bangalore|bengaluru|delhi|noida|gurugram|hyderabad|pune|mumbai|chennai|germany|berlin|munich|freiburg"
    r"|paris|france|mexico|brazil|singapore|japan|tokyo|australia|sydney|melbourne|israel|tel aviv|ireland|dublin"
    r"|limerick|netherlands|amsterdam|spain|madrid|barcelona|poland|warsaw|krak[oó]w|switzerland|zurich|latam|apac|korea"
    r"|seoul|china|taiwan|argentina|buenos aires|colombia|bogot[aá]|medell[ií]n|portugal|lisbon|italy|sweden|stockholm"
    r"|denmark|finland|helsinki|norway|philippines|vietnam|nigeria|kenya|uae|dubai|pakistan|karachi|indonesia|jakarta"
    r"|budapest|bucharest|romania|kyiv|ukraine|cape town|s[aã]o paulo|vilnius|astana|cyprus",
    re.I,
)
# Descriptors that say how, not where: dropped. "(Remote)" style descriptors set the remote flag instead.
_DESCRIPTOR = re.compile(
    r"\b(hq|headquarters|office|offices|hybrid|on-?site|in[- ]office|in person|metro|area|flatiron|soho|main site"
    r"|est|edt|pst|time ?zones?|coast|only)\b",
    re.I,
)
_REMOTE = re.compile(r"\b(remote|anywhere|distributed|wfh|work from home)\b", re.I)
_REMOTE_FILLER = re.compile(r"\b(friendly|ok|first|within|based|in the|in|from)\b", re.I)


def _split_top(s: str) -> list[str]:
    """Split on / ; | ' or ' ' and ' outside parentheses."""
    out, depth, buf, i = [], 0, "", 0
    while i < len(s):
        ch = s[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0:
            m = re.match(r"\s+/\s+|;|\s\|\s|\|(?=\s)|,?\s+or\s+|,?\s+and\s+|\s+&\s+", s[i:], re.I)
            if m:
                out.append(buf)
                buf = ""
                i += m.end()
                continue
        buf += ch
        i += 1
    out.append(buf)
    return [p.strip(" ,-–:") for p in out if p.strip(" ,-–:")]


def _city_state(city: str, st: str) -> str:
    c = re.sub(r"\s+", " ", city).strip(" .-–:")
    known = CITIES.get(c.lower())
    if known and known[1] == st:
        c = known[0]
    elif c == c.lower():
        c = c.title()
    else:  # "San francisco" -> "San Francisco"
        c = " ".join(w[:1].upper() + w[1:] if w.islower() and len(w) > 2 else w for w in c.split(" "))
    return f"{c}, {st}"


def _state_code(s: str) -> str | None:
    t = re.sub(r"\s*\d{5}(-\d{4})?$", "", s.strip()).strip(" .")
    if t.upper() in STATES and len(t) == 2:
        return t.upper()
    if t.replace(".", "").upper() == "DC":
        return "DC"
    return _STATE_BY_NAME.get(t.lower())


def _place(piece: str) -> list[str] | None:
    """One place (no remote words) -> display strings; [] when not a place; None when outside the US."""
    s = re.sub(r"\s+", " ", piece).strip(" ,-–:()")
    if not s:
        return []
    if _GLOBAL.match(s) or _US_COUNTRY.match(s):
        return ["United States"]
    if _BAY.match(s):
        return [BAY_AREA]
    has_us = bool(re.search(r"(,|\b)\s*(us|usa|united states)\s*$", s, re.I))
    if _NON_US.search(s) and not has_us:
        return None
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) >= 2 and _US_COUNTRY.match(parts[-1]):
        parts = parts[:-1]
    elif len(parts) >= 3 and re.fullmatch(r"[A-Za-z]{2}", parts[-1]) and (
        parts[-1].upper() not in STATES or (re.fullmatch(r"[A-Za-z]{2}", parts[-2]) and parts[-2].upper() not in STATES)
    ):
        return None  # "City, Region, CC" with a non-US country code ("Toronto, ON, CA")
    if len(parts) == 1:
        p = parts[0]
        if "/" in p:
            return [x for sub in p.split("/") for x in (_place(sub) or [])]
        known = CITIES.get(p.lower())
        if known:
            return [f"{known[0]}, {known[1]}"]
        m = re.fullmatch(r"(.+?)\s+([A-Z]{2})", p)  # "Long Beach CA"
        if m and m.group(2) in STATES and not _state_code(p):
            return [_city_state(m.group(1), m.group(2))]
        if re.fullmatch(r"[A-Za-z]{2}", p) and p.upper() not in ("US", "DC") and not has_us:
            return None  # a bare two-letter token in board lists is a country code ("IN", "CA", "MX")
        known = CITIES.get(p.lower())
        if known:
            return [f"{known[0]}, {known[1]}"]
        st = _state_code(p)
        if st:
            return [STATES[st]]
        # Lever writes "Massachusetts - Boston".
        dash = [part.strip() for part in re.split(r"\s[-–]\s", p, maxsplit=1)]
        if len(dash) == 2:
            st = _state_code(dash[0])
            if st and dash[1] and not _state_code(dash[1]):
                return [_city_state(dash[1], st)]
            st = _state_code(dash[1])
            if st and dash[0] and not _state_code(dash[0]):
                return [_city_state(dash[0], st)]
        return []
    # several "City, ST" pairs in one string
    pairs = re.findall(r"([^,]+?),\s*([A-Z]{2})\b(?!\w)", s)
    pairs = [(c, st) for c, st in pairs if st in STATES]
    if len(pairs) >= 2:
        return [_city_state(c, st) for c, st in pairs]
    # "City, ST" / "City, State" / "Street, City, ST 94105"
    for i in range(len(parts) - 1, 0, -1):
        st = _state_code(parts[i])
        if st:
            city = parts[i - 1]
            if re.fullmatch(r"[A-Z]{2}", city.strip()) and (city.strip() in STATES or city.strip().lower() not in CITIES):
                break  # "NY, DC": a list of places, not "City, ST"
            if "/" in city:
                city = city.split("/")[0]
            if _BAY.match(city.strip()):
                return [BAY_AREA]
            return [_city_state(city, st)]
    # "NY, SF" / "Los Angeles, San Francisco, Austin": a list of known cities
    if all(p.lower() in CITIES for p in parts):
        return [f"{CITIES[p.lower()][0]}, {CITIES[p.lower()][1]}" for p in parts]
    # "Washington, United States" style, already stripped of the country
    known = CITIES.get(parts[0].lower())
    if known:
        return [f"{known[0]}, {known[1]}"]
    st = _state_code(parts[0])
    if st:
        return [STATES[st]]
    return []


def _piece(piece: str, remote_ctx: bool = False) -> tuple[list[str], bool, bool]:
    """-> (places, remote, saw_us). places may hold 'United States' for country-level mentions."""
    places: list[str] = []
    saw_us = False
    remote = remote_ctx or bool(_REMOTE.search(piece))
    inner_found = False
    # parenthetical groups: descriptors, or more places ("Remote (Seattle, WA, US; NY)")
    for inner in re.findall(r"\(([^()]*)\)?", piece):
        for sub in _split_top(inner.replace(";", " ; ")):
            sub = sub.strip()
            if not sub:
                continue
            if _REMOTE.fullmatch(sub.strip()) or re.fullmatch(r"remote[\w -]*", sub, re.I):
                remote = True
                continue
            cleaned = _DESCRIPTOR.sub("", sub).strip(" ,-–:")
            if not cleaned:
                continue
            res = _place(cleaned)
            inner_found = True
            if res:
                places += res
                saw_us = True
    outer = re.sub(r"\([^()]*\)?", " ", piece)
    outer = _REMOTE.sub(" ", outer)
    outer = _REMOTE_FILLER.sub(" ", outer) if remote else outer
    outer = _DESCRIPTOR.sub(" ", outer)
    outer = re.sub(r"\s+", " ", outer).strip(" ,-–:/")
    if outer:
        res = _place(outer)
        if res is None and not places:
            return [], remote, False
        if res:
            places = res + places
            saw_us = True
        elif res == [] and remote and not inner_found:
            places.append("United States")  # "AMER - US - Remote": remote, no non-US place named
            saw_us = True
    elif remote and not inner_found:
        places.append("United States")  # bare "Remote"
        saw_us = True
    return places, remote, saw_us


def pretty_locations(raw: list[str] | None) -> tuple[list[str], bool]:
    """Clean a job's raw location strings -> (US display locations, remote)."""
    cities: list[str] = []
    states: list[str] = []
    country = False
    remote = False
    for r in raw or []:
        for piece in _split_top(r or ""):
            places, rem, us = _piece(piece)
            if not us:
                continue
            remote = remote or rem
            for p in places:
                if p == "United States":
                    country = True
                elif p in STATES.values() and "," not in p:
                    states.append(p)
                else:
                    cities.append(p)
    uniq = lambda xs: list(dict.fromkeys(xs))
    if cities:
        return uniq(cities), remote
    if states:
        return uniq(states), remote
    if country:
        return ["Remote (US)" if remote else "United States"], remote
    return [], False
