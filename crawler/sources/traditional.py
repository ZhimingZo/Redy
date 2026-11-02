"""Recognizable US companies outside software and internet tech.

The list is an explicit set of household names from the S&P 500: banks, insurers,
retailers, consumer brands, healthcare, autos, airlines, telecom, energy, and
the industrial names people already know. It is not every non-tech S&P company.
A legal headquarters abroad does not drop a US company. Chinese companies are
not in the set.
"""
from __future__ import annotations

import re

# Ticker -> official registrable domain. Only names on this map are kept.
_SITES = {
    # Banks, cards, brokers, insurers
    "JPM": "jpmorganchase.com",
    "BAC": "bankofamerica.com",
    "WFC": "wellsfargo.com",
    "C": "citigroup.com",
    "GS": "goldmansachs.com",
    "MS": "morganstanley.com",
    "AXP": "americanexpress.com",
    "V": "visa.com",
    "MA": "mastercard.com",
    "COF": "capitalone.com",
    "SCHW": "schwab.com",
    "BLK": "blackrock.com",
    "BX": "blackstone.com",
    "KKR": "kkr.com",
    "USB": "usbank.com",
    "PNC": "pnc.com",
    "TFC": "truist.com",
    "BNY": "bny.com",
    "STT": "statestreet.com",
    "BRK.B": "berkshirehathaway.com",
    "AFL": "aflac.com",
    "ALL": "allstate.com",
    "PGR": "progressive.com",
    "TRV": "travelers.com",
    "MET": "metlife.com",
    "PRU": "prudential.com",
    "AIG": "aig.com",
    "CB": "chubb.com",
    "HIG": "thehartford.com",
    "CME": "cmegroup.com",
    "ICE": "ice.com",
    "NDAQ": "nasdaq.com",
    "SPGI": "spglobal.com",
    "MCO": "moodys.com",
    "HOOD": "robinhood.com",
    "TROW": "troweprice.com",
    "IBKR": "interactivebrokers.com",
    "MRSH": "marsh.com",
    # Healthcare and pharma
    "JNJ": "jnj.com",
    "PFE": "pfizer.com",
    "LLY": "lilly.com",
    "MRK": "merck.com",
    "ABBV": "abbvie.com",
    "AMGN": "amgen.com",
    "GILD": "gilead.com",
    "BMY": "bms.com",
    "BIIB": "biogen.com",
    "REGN": "regeneron.com",
    "VRTX": "vrtx.com",
    "MRNA": "modernatx.com",
    "UNH": "unitedhealthgroup.com",
    "ELV": "elevancehealth.com",
    "CI": "cigna.com",
    "HUM": "humana.com",
    "CVS": "cvshealth.com",
    "CNC": "centene.com",
    "ABT": "abbott.com",
    "MDT": "medtronic.com",
    "SYK": "stryker.com",
    "BSX": "bostonscientific.com",
    "ISRG": "intuitive.com",
    "EW": "edwards.com",
    "ZBH": "zimmerbiomet.com",
    "DXCM": "dexcom.com",
    "GEHC": "gehealthcare.com",
    "TMO": "thermofisher.com",
    "DHR": "danaher.com",
    "MCK": "mckesson.com",
    "CAH": "cardinalhealth.com",
    "HCA": "hcahealthcare.com",
    "DVA": "davita.com",
    "LH": "labcorp.com",
    "DGX": "questdiagnostics.com",
    "ZTS": "zoetis.com",
    "BDX": "bd.com",
    "BAX": "baxter.com",
    "ALGN": "aligntech.com",
    # Industrials, airlines, defense
    "MMM": "3m.com",
    "BA": "boeing.com",
    "CAT": "caterpillar.com",
    "DE": "deere.com",
    "DAL": "delta.com",
    "GE": "geaerospace.com",
    "GEV": "gevernova.com",
    "GD": "gd.com",
    "HON": "honeywell.com",
    "LMT": "lockheedmartin.com",
    "NOC": "northropgrumman.com",
    "RTX": "rtx.com",
    "LHX": "l3harris.com",
    "TXT": "textron.com",
    "UPS": "ups.com",
    "FDX": "fedex.com",
    "LUV": "southwest.com",
    "UAL": "united.com",
    "CSX": "csx.com",
    "UNP": "up.com",
    "NSC": "norfolksouthern.com",
    "EMR": "emerson.com",
    "ETN": "eaton.com",
    "ROK": "rockwellautomation.com",
    "CMI": "cummins.com",
    "PCAR": "paccar.com",
    "SWK": "stanleyblackanddecker.com",
    "OTIS": "otis.com",
    "CARR": "carrier.com",
    "JCI": "johnsoncontrols.com",
    "TT": "tranetechnologies.com",
    "WM": "wm.com",
    "CTAS": "cintas.com",
    "ADP": "adp.com",
    "PAYX": "paychex.com",
    "EFX": "equifax.com",
    "AXON": "axon.com",
    "GNRC": "generac.com",
    "SNA": "snapon.com",
    "DD": "dupont.com",
    # Energy
    "XOM": "exxonmobil.com",
    "CVX": "chevron.com",
    "COP": "conocophillips.com",
    "SLB": "slb.com",
    "HAL": "halliburton.com",
    "BKR": "bakerhughes.com",
    "MPC": "marathonpetroleum.com",
    "PSX": "phillips66.com",
    "VLO": "valero.com",
    "OXY": "oxy.com",
    "KMI": "kindermorgan.com",
    # Consumer brands and retail
    "KO": "coca-colacompany.com",
    "PEP": "pepsico.com",
    "PG": "pg.com",
    "PM": "pmi.com",
    "MO": "altria.com",
    "COST": "costco.com",
    "WMT": "walmart.com",
    "TGT": "target.com",
    "KR": "kroger.com",
    "DG": "dollargeneral.com",
    "DLTR": "dollartree.com",
    "CL": "colgatepalmolive.com",
    "CLX": "clorox.com",
    "KMB": "kimberly-clark.com",
    "KHC": "kraftheinzcompany.com",
    "MDLZ": "mondelezinternational.com",
    "GIS": "generalmills.com",
    "HSY": "thehersheycompany.com",
    "SJM": "jmsmucker.com",
    "HRL": "hormelfoods.com",
    "TSN": "tysonfoods.com",
    "KDP": "keurigdrpepper.com",
    "MNST": "monsterenergy.com",
    "STZ": "cbrands.com",
    "TAP": "molsoncoors.com",
    "EL": "elcompanies.com",
    "KVUE": "kenvue.com",
    "MKC": "mccormickcorporation.com",
    "SYY": "sysco.com",
    "ADM": "adm.com",
    "NKE": "nike.com",
    "SBUX": "starbucks.com",
    "MCD": "mcdonalds.com",
    "CMG": "chipotle.com",
    "DPZ": "dominos.com",
    "YUM": "yum.com",
    "HD": "homedepot.com",
    "LOW": "lowes.com",
    "BBY": "bestbuy.com",
    "AZO": "autozone.com",
    "ORLY": "oreillyauto.com",
    "TSCO": "tractorsupply.com",
    "ROST": "rossstores.com",
    "TJX": "tjx.com",
    "ULTA": "ulta.com",
    "WSM": "williams-sonomainc.com",
    "RL": "ralphlauren.com",
    "HAS": "hasbro.com",
    "F": "ford.com",
    "GM": "gm.com",
    "BKNG": "bookingholdings.com",
    "EXPE": "expediagroup.com",
    "MAR": "marriott.com",
    "HLT": "hilton.com",
    "CCL": "carnivalcorp.com",
    "RCL": "rclinvestor.com",
    "NCLH": "nclhltd.com",
    "MGM": "mgmresorts.com",
    "LVS": "sands.com",
    "WYNN": "wynnresorts.com",
    "CVNA": "carvana.com",
    "EBAY": "ebay.com",
    "DHI": "drhorton.com",
    "LEN": "lennar.com",
    "PHM": "pultegroup.com",
    # Telecom and media
    "T": "att.com",
    "VZ": "verizon.com",
    "TMUS": "t-mobile.com",
    "CMCSA": "corporate.comcast.com",
    "CHTR": "corporate.charter.com",
    "DIS": "thewaltdisneycompany.com",
    "WBD": "wbd.com",
    "PSKY": "paramount.com",
    "FOXA": "foxcorporation.com",
    "NWSA": "newscorp.com",
    "LYV": "livenationentertainment.com",
    "TKO": "tkogrp.com",
    # Chemicals, paint, utilities people know by name
    "DOW": "dow.com",
    "SHW": "sherwin-williams.com",
    "DUK": "duke-energy.com",
    "SO": "southerncompany.com",
    "NEE": "nexteraenergy.com",
    "EXC": "exeloncorp.com",
    "D": "dominionenergy.com",
    "PCG": "pge.com",
    "ED": "coned.com",
}

_NAME = {"LLY": "Eli Lilly"}
_WEBSITE = {"SJM": "https://www.jmsmucker.com"}


def _public_name(ticker: str, security: str) -> str:
    if ticker in _NAME:
        return _NAME[ticker]
    name = re.sub(r"\s*\(Class [A-Z]\)\s*$", "", (security or "").strip())
    return re.sub(r"\s*\(The\)\s*$", "", name).strip()


def select_traditional(rows: list[dict]) -> list[dict]:
    """S&P rows kept only when the ticker is a recognizable US company on _SITES."""
    seen: set[str] = set()
    out = []
    for row in rows:
        ticker = (row.get("Symbol") or "").strip()
        domain = _SITES.get(ticker)
        if not domain:
            continue
        name = _public_name(ticker, row.get("Security") or "")
        key = re.sub(r"[^a-z0-9]+", "", name.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        hq = re.sub(r"\[\d+\]", "", row.get("Headquarters Location") or "").strip()
        website = _WEBSITE.get(ticker) or f"https://{domain}"
        out.append({
            "name": name,
            "ticker": ticker,
            "kind": "traditional",
            "sector": row.get("GICS Sector") or "",
            "hq": hq,
            "website": website,
            "domain": domain,
        })
    out.sort(key=lambda c: c["name"].lower())
    return out


def build_document(rows: list[dict]) -> dict:
    return {
        "note": (
            "Recognizable United States companies outside software and internet tech: "
            "banks, insurers, retailers, consumer brands, healthcare, autos, airlines, "
            "telecom, energy, and industrial names people know. Not the long tail of the "
            "S&P 500. A legal headquarters abroad does not drop a US company. No Chinese companies."
        ),
        "companies": select_traditional(rows),
    }
