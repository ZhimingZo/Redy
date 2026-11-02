from crawler.sources.bestbuy import bestbuy_places, job_rows, pay_range, posted_at as bestbuy_posted
from crawler.sources.fedex import fedex_places, page_url, preload
from crawler.sources.goldman import goldman_places, keep as goldman_keep
from crawler.sources.phenom import job_url, phenom_places, search_payload
from crawler.sources.walmart import posted_at, walmart_place


def test_phenom_search_payload_reads_the_embedded_object():
    html = """
    <script>{"eagerLoadRefineSearch":{"totalHits":2,"hits":1,"data":{"jobs":[
      {"jobId":"R1","title":"Staff Software Engineer","cityState":"Indianapolis, Indiana",
       "country":"United States of America","postedDate":"2026-10-01T00:00:00.000+0000",
       "multi_location_array":[{"location":"Indianapolis, Indiana, United States of America"}]}
    ]}}}</script>
    """
    payload = search_payload(html)
    job = payload["data"]["jobs"][0]
    assert payload["totalHits"] == 2
    assert phenom_places(job) == ["Indianapolis, Indiana, United States of America"]
    assert job_url("https://careers.lilly.com/us/en/search-results", job).endswith("/job/R1/Staff-Software-Engineer")


def test_goldman_keeps_a_us_engineer_and_drops_bengaluru():
    us = {"jobTitle": "Software Engineer, Vice President", "locations": [{"city": "New York", "state": "NY", "country": "United States"}]}
    india = {"jobTitle": "Software Engineer", "locations": [{"city": "Bengaluru", "state": "", "country": "India"}]}
    assert goldman_places(us) == ["New York, NY, United States"]
    assert goldman_keep(us["jobTitle"], goldman_places(us))
    assert not goldman_keep(india["jobTitle"], goldman_places(india))


def test_fedex_preload_and_us_place():
    html = """
    <script>
    window.__PRELOAD_STATE__ = {"jobSearch":{"totalJob":1,"jobs":[
      {"reference":"P1","title":"Software Engineer","originalURL":"software-engineer/job/P1",
       "locations":[{"city":"Memphis","stateAbbr":"TN","country":"United States"}]}
    ]}};
    window.__BUILD__ = 'abc';
    </script>
    """
    job = preload(html)["jobSearch"]["jobs"][0]
    assert fedex_places(job) == ["Memphis, TN, United States"]
    assert page_url("https://careers.fedex.com/jobs?keyword=software+engineer", 2) == (
        "https://careers.fedex.com/jobs/page/2?keyword=software+engineer"
    )


def test_bestbuy_row_place_pay_and_day():
    data = {"items": {"features": [{"properties": {
        "title": "Engineer II", "auto_req_id": "1BR", "city": "Richfield", "state": "Minnesota",
        "country": "United States",
    }}]}}
    row = job_rows(data)[0]
    assert bestbuy_places(row) == ["Richfield, Minnesota, United States"]
    assert bestbuy_posted("29-Oct-2026") == "2026-10-29T12:00:00+00:00"
    assert pay_range({"pay_min": "86250", "pay_max": "152550", "pay_currency": "USD"}) == [86250, 152550, "USD", "year"]


def test_walmart_place_and_posted_day():
    job = {"city": "SUNNYVALE", "state": "CA", "country": "US", "jobPostingStartDate": 1793232000000}
    assert walmart_place(job) == ["Sunnyvale, CA, United States"]
    assert posted_at(job["jobPostingStartDate"]).endswith("T12:00:00+00:00")
