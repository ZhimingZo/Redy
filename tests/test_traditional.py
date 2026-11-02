"""The traditional track is recognizable US companies, not the rest of the S&P 500."""
from crawler.normalize import build
from crawler.sources.traditional import select_traditional
from datetime import datetime, timezone


def test_traditional_keeps_household_names_only():
    rows = [
        {"Symbol": "WMT", "Security": "Walmart", "GICS Sector": "Consumer Staples",
         "GICS Sub-Industry": "Consumer Staples Merchandise Retail", "Headquarters Location": "Bentonville, Arkansas"},
        {"Symbol": "JPM", "Security": "JPMorgan Chase", "GICS Sector": "Financials",
         "GICS Sub-Industry": "Diversified Banks", "Headquarters Location": "New York City, New York"},
        {"Symbol": "MDT", "Security": "Medtronic", "GICS Sector": "Health Care",
         "GICS Sub-Industry": "Health Care Equipment", "Headquarters Location": "Galway, Ireland"},
        {"Symbol": "LLY", "Security": "Lilly (Eli)", "GICS Sector": "Health Care",
         "GICS Sub-Industry": "Pharmaceuticals", "Headquarters Location": "Indianapolis, Indiana"},
        {"Symbol": "HD", "Security": "Home Depot (The)", "GICS Sector": "Consumer Discretionary",
         "GICS Sub-Industry": "Home Improvement Retail", "Headquarters Location": "Atlanta, Georgia"},
        {"Symbol": "AOS", "Security": "A. O. Smith", "GICS Sector": "Industrials",
         "GICS Sub-Industry": "Building Products", "Headquarters Location": "Milwaukee, Wisconsin"},
        {"Symbol": "LULU", "Security": "Lululemon Athletica", "GICS Sector": "Consumer Discretionary",
         "GICS Sub-Industry": "Apparel, Accessories & Luxury Goods", "Headquarters Location": "Vancouver, Canada"},
        {"Symbol": "NVDA", "Security": "Nvidia", "GICS Sector": "Information Technology",
         "GICS Sub-Industry": "Semiconductors", "Headquarters Location": "Santa Clara, California"},
        {"Symbol": "FITB", "Security": "Fifth Third Bancorp", "GICS Sector": "Financials",
         "GICS Sub-Industry": "Regional Banks", "Headquarters Location": "Cincinnati, Ohio"},
    ]
    names = [c["name"] for c in select_traditional(rows)]
    assert names == ["Eli Lilly", "Home Depot", "JPMorgan Chase", "Medtronic", "Walmart"]
    medtronic = next(c for c in select_traditional(rows) if c["ticker"] == "MDT")
    assert medtronic["domain"] == "medtronic.com"
    assert medtronic["hq"] == "Galway, Ireland"


def test_traditional_source_is_not_labeled_a16z():
    raw = {
        "co": "Walmart", "cslug": "walmart", "dom": "walmart.com", "src": "traditional", "p": "greenhouse",
        "id": "1", "t": "Senior Software Engineer", "loc": ["Bentonville, AR"],
        "url": "https://example.com/1", "at": "2026-10-01T00:00:00Z",
        "yoe": ["5+ years of software experience"],
    }
    data = build([raw], datetime(2026, 10, 31, tzinfo=timezone.utc))
    assert data["companies"]["walmart.com"]["backers"] == []
    assert len(data["jobs"]) == 1
