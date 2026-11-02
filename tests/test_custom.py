from crawler.sources.custom import (
    amd_pay,
    amd_places,
    microsoft_places,
    parse_apple,
    parse_google,
    parse_meta,
    anduril_places,
    pcsx_places,
    paypal_places,
    radancy_rows,
    salesforce_places,
    salesforce_rows,
    supermicro_rows,
    tesla_rows,
    _record,
)


def test_amd_place_uses_city_and_state():
    assert amd_places({"city": "Santa Clara", "state": "California", "country": "United States"}) == ["Santa Clara, CA"]
    assert amd_places({"city": "Home", "state": "Texas", "country": "United States"}) == ["Remote, Texas"]
    assert amd_places({"state": "Texas", "country": "United States", "location_name": "US,TX,Home Office"}) == ["Remote, Texas"]
    assert amd_places({"city": "Home Office_Area1", "state": "California", "country": "United States"}) == ["Remote, California"]
    assert amd_places({"location_name": "US,CA,Santa Clara", "country": "United States"}) == ["Santa Clara, CA"]
    assert amd_places({"city": "Hyderabad", "state": "Telangana", "country": "India"}) == []
    assert amd_pay({"tags2": ["USD $150,500.00/Yr."], "tags3": ["USD $215,000.00/Yr."]}) == "$150,500 - $215,000"


def test_apple_card_keeps_the_location_suffix():
    html = '''
    <a href="/en-us/details/200692779-0836/senior-ios-app-development-engineer?team=SFTWR">Senior iOS App Development Engineer</a>
    <span class="job-posted-date">Nov 01, 2026</span>
    <span id="search-store-name-container-1">Cupertino</span>
    '''
    rows = parse_apple(html)
    assert rows[0]["id"] == "200692779-0836"
    assert rows[0]["title"].startswith("Senior iOS")
    assert rows[0]["locs"] == ["Cupertino"]
    assert rows[0]["posted"] == "2026-11-01T12:00:00+00:00"
    assert rows[0]["url"] == "https://jobs.apple.com/en-us/details/200692779-0836/senior-ios-app-development-engineer"


def test_google_card_reads_every_listed_city():
    html = '''
    <li class="lLd3Je" ssk='17:73091418758750918'><h3 class="QJPWVe">Staff Software Engineer</h3>
    <span class="r0wTof ">Austin, TX, USA</span><span class="r0wTof p3oCrc">Atlanta, GA, USA</span>
    '''
    rows = parse_google(html)
    assert rows == [{
        "id": "73091418758750918",
        "title": "Staff Software Engineer",
        "locs": ["Austin, TX, USA", "Atlanta, GA, USA"],
        "posted": None,
        "url": "https://www.google.com/about/careers/applications/jobs/results/73091418758750918",
    }]


def test_meta_card_reads_the_job_id():
    html = '''
    <a href="/profile/job_details/1690022942358388"><h3>Software Engineer, Infrastructure</h3>
    <span>Sunnyvale, CA</span><span>Bellevue, WA</span></a>
    '''
    rows = parse_meta(html)
    assert rows[0]["id"] == "1690022942358388"
    assert rows[0]["locs"] == ["Sunnyvale, CA", "Bellevue, WA"]
    assert "metacareers.com/profile/job_details/1690022942358388" in rows[0]["url"]


def test_tesla_joins_the_location_name():
    state = {
        "lookup": {"locations": {
            "401022": "Palo Alto, California",
            "2": "Fremont, California",
            "9": "Toronto, Ontario",
        }},
    }
    state["listings"] = [
        {"id": "224501", "t": "Senior Software Engineer", "l": "401022"},
        {"id": "2", "t": "Staff Software Engineer", "l": "2"},
        {"id": "9", "t": "Senior Software Engineer", "l": "9"},
    ]
    rows = tesla_rows(state)
    kept = [_record("tesla", r["id"], r["title"], r["locs"], r["url"], None, "") for r in rows]
    assert [r["id"] for r in kept if r] == ["224501", "2"]
    assert rows[1]["locs"] == ["Fremont, CA"]


def test_microsoft_place_drops_the_country_code():
    assert microsoft_places(["US", "Redmond, WA, US", "United States, Multiple Locations, Multiple Locations"]) == ["Redmond, WA"]


def test_salesforce_place_uses_the_state_and_city():
    places = salesforce_places({
        "Countries": ["United States of America"],
        "Job_Requisition_Primary_Location": "Virginia - Mclean",
        "Locations": ["Bellevue", "Remote"],
    })
    assert places[0] == "Mclean, VA"
    assert "Bellevue" in places
    assert "Remote, United States" in places
    assert salesforce_places({"Countries": ["Ireland"], "Locations": ["Dublin"]}) == []


def test_salesforce_row_keeps_the_workday_link():
    rows = salesforce_rows([{
        "Job_Requisition_Ref_ID": "JR321396",
        "Job_Posting_Title": "Lead Network Engineer",
        "Countries": ["United States of America"],
        "Job_Requisition_Primary_Location": "Virginia - Mclean",
        "Locations": ["Mclean"],
        "External_Job_Posting_Start_Date": "2026-10-09",
        "External_Job_Posting_Site": "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site/job/JR321396",
        "Job_Description": "<p>Run the backbone.</p>",
    }])
    assert rows[0]["url"].startswith("https://salesforce.wd12.myworkdayjobs.com/")
    assert rows[0]["posted"] == "2026-10-09T12:00:00+00:00"
    kept = _record("salesforce", rows[0]["id"], rows[0]["title"], rows[0]["locs"], rows[0]["url"], rows[0]["posted"], rows[0]["desc"])
    assert kept and kept["dom"] == "salesforce.com"


def test_junior_title_is_dropped():
    assert _record("google", "1", "Software Engineer I", ["Austin, TX, USA"], "https://example.com", None, "") is None


def test_anduril_place_uses_the_state_name():
    assert anduril_places(["Quincy, Massachusetts, United States", "London, United Kingdom"]) == ["Quincy, MA"]
    assert anduril_places(["Washington, District of Columbia, United States"]) == ["Washington, DC"]


def test_pcsx_places_cover_the_career_pages():
    assert pcsx_places(["Denver, CO, US", "CO,US"]) == ["Denver, CO"]
    assert pcsx_places(["CO,US"], ["Colorado - Remote, United States of America"]) == ["Remote, CO"]
    assert pcsx_places(["ID,US"], ["All Cities, Idaho, United States of America"]) == ["Remote, ID"]
    assert pcsx_places(["Lehi, UT, US"]) == ["Lehi, UT"]
    assert pcsx_places(["CO,US"], ["US-CO-Colorado-Remote (1335)"]) == ["Remote, CO"]
    assert pcsx_places(["Vancouver, BC, CA"], ["AMER - Canada - British Columbia - Vancouver"]) == []
    assert pcsx_places([], ["AMER - United States - California - San Francisco - One Market"]) == ["San Francisco, CA"]
    assert pcsx_places(["CA,US"], ["California Non-Bay Area - Remote, United States of America"]) == ["Remote, CA"]


def test_radancy_keeps_each_us_city():
    html = '''<a href="/job/morrisville/finance/27600/102"><h3>Finance Manager</h3><span class="job-location job-default">Morrisville, North Carolina, United States; Cork, Munster, Ireland</span></a>'''
    rows = radancy_rows(html, "https://careers.netapp.com")
    assert rows[0]["title"] == "Finance Manager"
    assert rows[0]["locs"] == ["Morrisville, NC"]


def test_paypal_places_keep_cities():
    assert paypal_places(["CA,US", "San Jose, CA, US", "Scottsdale, AZ, US"]) == ["San Jose, CA", "Scottsdale, AZ"]
    assert paypal_places(["CA,US", "AL,US"]) == ["United States"]


def test_radancy_reads_locale_job_links():
    html = '''<a href="/en/job/melbourne/specialist/4832/101109854016"><h2>Software Engineer</h2><span class="location">Melbourne, Florida</span></a>
    <a href="/en/job/montreal/associate/42849/101385203504"><h2>Technical Associate</h2><span class="location">Montreal, Quebec</span></a>'''
    rows = radancy_rows(html, "https://careers.l3harris.com")
    assert [row["id"] for row in rows] == ["101109854016", "101385203504"]
    assert rows[0]["locs"] == ["Melbourne, FL"]
    assert rows[1]["locs"] == []
    assert rows[0]["url"].startswith("https://careers.l3harris.com/en/job/")


def test_radancy_row_uses_city_and_state():
    html = '''<a href="/job/mountain-view/software-engineer-2/27595/102345437808"><h2>Software Engineer 2</h2><span class="job-location">Mountain View, California</span></a>'''
    rows = radancy_rows(html, "https://jobs.intuit.com")
    assert rows[0]["id"] == "102345437808"
    assert rows[0]["locs"] == ["Mountain View, CA"]
    assert rows[0]["url"] == "https://jobs.intuit.com/job/mountain-view/software-engineer-2/27595/102345437808"


def test_supermicro_row_drops_other_countries():
    html = '''
    <a href="/job/Netherlands-X/1426905900/" class="jobTitle-link">RMA Coordinator</a>
    <span class="jobLocation">Netherlands - Hertogenbosch</span>
    <a href="/job/United-States-San-Jose/1400000001/" class="jobTitle-link">Software Engineer</a>
    <span class="jobLocation">United States - San Jose, California</span>
    '''
    rows = supermicro_rows(html)
    assert [row["id"] for row in rows] == ["1400000001"]
    assert rows[0]["locs"] == ["San Jose, CA"]
    assert supermicro_rows(
        '<a href="/job/Chennai-X/9/" class="jobTitle-link">Engineer</a><span class="jobLocation">Chennai, Tamil Nadu, IN</span>'
    ) == []
    assert rows[0]["url"].startswith("https://jobs.supermicro.com/job/")
    moved = supermicro_rows(
        '<a href="/job/United-States-Hershey/7/" class="jobTitle-link">Engineer</a><span class="jobLocation">United States - Hershey, Pennsylvania</span>',
        "https://careers.thehersheycompany.com",
    )
    assert moved[0]["url"].startswith("https://careers.thehersheycompany.com/job/")
