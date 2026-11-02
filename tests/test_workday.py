from datetime import date

from crawler.locations import pretty_locations
from crawler.sources.workday import (
    choose_sites,
    discover_sites,
    list_postings,
    posted_at,
    worth_detail,
    workday_place,
)


def test_discover_site_from_careers_html():
    html = '"url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/login"'
    assert discover_sites(html) == [("nvidia.wd5.myworkdayjobs.com", "NVIDIAExternalCareerSite")]
    escaped = "https://\\u002fflir.wd1.myworkdayjobs.com/flircareers"
    assert discover_sites(escaped) == [("flir.wd1.myworkdayjobs.com", "flircareers")]
    assert discover_sites("https://coke.wd1.myworkdayjobs.com/coca-cola-careers") == [
        ("coke.wd1.myworkdayjobs.com", "coca-cola-careers")
    ]
    assert discover_sites("https://citi.wd5.myworkdayjobs.com/en-US/2/userHome") == [
        ("citi.wd5.myworkdayjobs.com", "2")
    ]
    assert discover_sites("https://citi.wd5.myworkdayjobs.com/en-US") == []


def test_choose_sites_drops_campus_boards():
    pairs = [
        ("att.wd1.myworkdayjobs.com", "ATTGeneral"),
        ("att.wd1.myworkdayjobs.com", "ATTcollege"),
        ("att.wd1.myworkdayjobs.com", "Cricket"),
    ]
    chosen = choose_sites(pairs)
    assert ("att.wd1.myworkdayjobs.com", "ATTcollege") not in chosen
    assert ("att.wd1.myworkdayjobs.com", "ATTGeneral") in chosen


def test_workday_place_becomes_city_state():
    assert workday_place("US, CA, Santa Clara") == "Santa Clara, CA"
    places, _remote = pretty_locations([workday_place("US, CA, Santa Clara")])
    assert places == ["Santa Clara, CA"]
    samples = {
        "USA - CA - San Jose (Ridder)": "San Jose, CA",
        "United States - California - Foster City": "Foster City, CA",
        "USA MD - Sparks - 7 Loveton Circle": "Sparks, MD",
        "USA CA - Milpitas 155": "Milpitas, CA",
        "USA NJ Branchburg": "Branchburg, NJ",
        "USA - New Jersey - Rahway": "Rahway, NJ",
        "Morristown,NJ-67 Park Place E": "Morristown, NJ",
        "Tampa Florida United States": "Tampa, FL",
        "New York New York United States": "New York, NY",
        "Irving Texas United States": "Irving, TX",
        "Charlotte North Carolina United States": "Charlotte, NC",
    }
    for raw, city in samples.items():
        assert workday_place(raw) == city
        assert pretty_locations([city])[0] == [city]


def test_posted_at_prefers_start_date():
    assert posted_at({"startDate": "2026-10-31", "postedOn": "Posted Today"}) == "2026-10-31T12:00:00+00:00"
    assert posted_at({"postedOn": "Posted 30+ Days Ago"}, today=date(2026, 10, 31)) == "2026-10-01T12:00:00+00:00"


def test_list_splits_at_the_2000_cap_and_keeps_us():
    calls = []

    def poster(host, tenant, site, facets, offset):
        calls.append((dict(facets), offset))
        if not facets:
            return {
                "total": 2000,
                "jobPostings": [],
                "facets": [{
                    "facetParameter": "locationMainGroup",
                    "values": [{
                        "facetParameter": "locationHierarchy1",
                        "descriptor": "Locations",
                        "values": [
                            {"descriptor": "United States of America", "id": "us", "count": 25},
                            {"descriptor": "India", "id": "in", "count": 400},
                        ],
                    }],
                }],
            }
        assert facets.get("locationHierarchy1") == ["us"]
        if offset == 0:
            return {
                "total": 25,
                "jobPostings": [
                    {"title": "Senior Software Engineer", "externalPath": f"/job/{i}", "locationsText": "US, CA, Santa Clara", "postedOn": "Posted Today"}
                    for i in range(20)
                ],
                "facets": [],
            }
        return {
            "total": 25,
            "jobPostings": [
                {"title": "Senior Software Engineer", "externalPath": f"/job/{i}", "locationsText": "US, CA, Santa Clara", "postedOn": "Posted Today"}
                for i in range(20, 25)
            ],
            "facets": [],
        }

    rows = list_postings("nvidia.wd5.myworkdayjobs.com", "NVIDIAExternalCareerSite", poster=poster)
    assert len(rows) == 25
    assert not any(c[0].get("locationHierarchy1") == ["in"] for c in calls)
    assert [c[1] for c in calls if c[0]] == [0, 20]


def test_a_failed_page_is_retried():
    calls = []

    def poster(host, tenant, site, facets, offset):
        calls.append(offset)
        if offset == 0:
            return {"total": 25, "jobPostings": [
                {"title": "Senior Software Engineer", "externalPath": f"/job/{i}", "locationsText": "US, CA, Santa Clara"}
                for i in range(20)
            ], "facets": []}
        if calls.count(20) == 1:
            return None
        return {"total": 25, "jobPostings": [
            {"title": "Senior Machine Learning Engineer – Perception & Embodied AI", "externalPath": "/job/embodied", "locationsText": "Mountain View, California"}
        ], "facets": []}

    rows = list_postings("generalmotors.wd5.myworkdayjobs.com", "Careers_GM", poster=poster)
    assert [r["externalPath"] for r in rows][-1] == "/job/embodied"
    assert calls.count(20) == 2


def test_country_facet_is_location_country_on_some_boards():
    def poster(host, tenant, site, facets, offset):
        if not facets:
            return {
                "total": 2000,
                "jobPostings": [],
                "facets": [{
                    "facetParameter": "locationMainGroup",
                    "values": [{
                        "facetParameter": "locationCountry",
                        "descriptor": "Country",
                        "values": [
                            {"descriptor": "India", "id": "in", "count": 30000},
                            {"descriptor": "United States of America", "id": "us", "count": 10},
                        ],
                    }],
                }],
            }
        assert facets == {"locationCountry": ["us"]}
        return {"total": 1, "jobPostings": [
            {"title": "Senior Software Engineer", "externalPath": "/job/us", "locationsText": "US, CA, San Jose", "postedOn": "Posted Today"}
        ], "facets": []}

    rows = list_postings("accenture.wd103.myworkdayjobs.com", "AccentureCareers", poster=poster)
    assert [r["externalPath"] for r in rows] == ["/job/us"]


def test_country_facet_named_country_and_jurisdiction():
    def poster(host, tenant, site, facets, offset):
        if not facets:
            return {
                "total": 2000,
                "jobPostings": [],
                "facets": [{
                    "facetParameter": "Country_and_Jurisdiction",
                    "values": [
                        {"descriptor": "United States of America", "id": "us", "count": 12},
                        {"descriptor": "India", "id": "in", "count": 900},
                    ],
                }],
            }
        assert facets == {"Country_and_Jurisdiction": ["us"]}
        return {"total": 1, "jobPostings": [
            {"title": "Senior Software Engineer", "externalPath": "/job/us", "locationsText": "Tampa Florida United States", "postedOn": "Posted Today"}
        ], "facets": []}

    rows = list_postings("citi.wd5.myworkdayjobs.com", "2", poster=poster)
    assert [r["externalPath"] for r in rows] == ["/job/us"]


def test_underscored_family_facet_splits_a_capped_us_board():
    calls = []

    def poster(host, tenant, site, facets, offset):
        calls.append(dict(facets))
        if not facets:
            return {
                "total": 2000,
                "jobPostings": [],
                "facets": [
                    {"facetParameter": "locationCountry", "values": [
                        {"descriptor": "United States of America", "id": "us", "count": 2381},
                    ]},
                    {"facetParameter": "Job_Family_Group", "values": [
                        {"descriptor": "Information Technology - Group", "id": "it", "count": 2},
                        {"descriptor": "Sales - Group", "id": "sales", "count": 30},
                    ]},
                ],
            }
        if "Job_Family_Group" not in facets:
            return {
                "total": 2381,
                "jobPostings": [],
                "facets": [
                    {"facetParameter": "Job_Family_Group", "values": [
                        {"descriptor": "Information Technology - Group", "id": "it", "count": 2},
                        {"descriptor": "Sales - Group", "id": "sales", "count": 30},
                    ]},
                ],
            }
        it = facets.get("Job_Family_Group") == ["it"]
        return {
            "total": 1,
            "jobPostings": [{
                "title": "Senior Software Engineer" if it else "Retail Sales Associate",
                "externalPath": "/job/it" if it else "/job/sales",
                "locationsText": "Bellevue, WA",
            }],
            "facets": [],
        }

    rows = list_postings("tmobile.wd1.myworkdayjobs.com", "External", poster=poster)
    assert {r["externalPath"] for r in rows} == {"/job/it", "/job/sales"}
    assert len(calls) == 4


def test_detail_skip_rules():
    assert worth_detail({"title": "Senior Software Engineer", "locationsText": "US, CA, Santa Clara"})
    assert worth_detail({"title": "Staff Software Engineer", "locationsText": "2 Locations"})
    assert not worth_detail({"title": "Software Engineer I", "locationsText": "US, CA, Santa Clara"})
    assert not worth_detail({"title": "Senior Software Engineer", "locationsText": "Bengaluru, India"})
    assert not worth_detail({"title": "Account Executive", "locationsText": "US, CA, Santa Clara"})
