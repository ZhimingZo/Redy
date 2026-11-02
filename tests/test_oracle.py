from crawler.sources.ats import smartrecruiters_row
from crawler.sources.oracle import active_sites, keep_row, oracle_rows


def test_active_sites_skip_alumni_and_inactive():
    items = [
        {"StatusCode": "ORA_ACTIVE", "SiteName": "JPMC Candidate Experience page", "SiteNumber": "CX_1001"},
        {"StatusCode": "ORA_ACTIVE", "SiteName": "Alumni Career Site", "SiteNumber": "CX_2001"},
        {"StatusCode": "ORA_INACTIVE", "SiteName": "Old site", "SiteNumber": "CX_3001"},
        {"StatusCode": "ORA_ACTIVE", "SiteName": "Chase- Candidate Experience page", "SiteNumber": "CX_1002"},
    ]
    assert active_sites(items) == ["CX_1001", "CX_1002"]


def test_oracle_row_keeps_a_us_engineer_and_drops_london():
    item = {"requisitionList": [
        {
            "Id": "210587260",
            "Title": "Lead Software Engineer - Backend Engineering",
            "PostedDate": "2026-10-31",
            "PrimaryLocation": "LONDON, LONDON, United Kingdom",
            "PrimaryLocationCountry": "GB",
            "WorkplaceType": "",
            "ShortDescriptionStr": "Build services.",
        },
        {
            "Id": "99",
            "Title": "Staff Software Engineer",
            "PostedDate": "2026-10-30",
            "PrimaryLocation": "AUSTIN, TX",
            "PrimaryLocationCountry": "US",
            "WorkplaceType": "Remote",
            "ShortDescriptionStr": "5+ years of experience. Pay is $180,000 to $220,000.",
            "secondaryLocations": [{"Name": "New York, NY, United States", "CountryCode": "US"}],
        },
    ]}
    rows = oracle_rows(item, "jpmc.fa.oraclecloud.com", "CX_1001")
    assert [row["id"] for row in rows if keep_row(row["t"], row["loc"])] == ["99"]
    kept = rows[1]
    assert kept["loc"][0] == "AUSTIN, TX, United States"
    assert kept["loc"][1] == "New York, NY, United States"
    assert kept["at"] == "2026-10-30T12:00:00+00:00"
    assert kept["url"].endswith("/sites/CX_1001/job/99")
    assert kept["pay"] == [180000, 220000, "USD", "year"]
    assert kept["rem"] is True


def test_smartrecruiters_uses_the_full_location():
    row = smartrecruiters_row("WynnResorts", {
        "id": "744000158847418",
        "name": "Senior Software Engineer",
        "releasedDate": "2026-10-30T21:49:18.224Z",
        "location": {
            "city": "Las Vegas",
            "region": "NV",
            "country": "us",
            "remote": False,
            "fullLocation": "Las Vegas, NV, United States",
        },
    })
    assert row["loc"] == ["Las Vegas, NV, United States"]
    assert row["url"] == "https://jobs.smartrecruiters.com/WynnResorts/744000158847418"
    assert keep_row(row["t"], row["loc"])
