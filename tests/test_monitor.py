from crawler.monitor import apply_check, page_signal, parse_posting, yc_listed
from crawler.normalize import desc_hash


def test_description_hash_ignores_whitespace_only():
    a = desc_hash("We are hiring a senior engineer who will own the public API and the on-call rotation for that service.\n\nYou will write the design doc.")
    b = desc_hash("We are hiring a senior engineer who will own the public API and the on-call rotation for that service.  You will write the design doc.")
    c = desc_hash("We are hiring a senior engineer who will own the public API and the on-call rotation for that service. You will write the design doc and the runbook.")
    assert a and a == b and a != c
    assert desc_hash("too short") is None


def test_parse_board_urls_and_greenhouse_jid():
    assert parse_posting("https://job-boards.greenhouse.io/axon/jobs/8107670003") == {
        "p": "greenhouse", "slug": "axon", "jid": "8107670003",
    }
    assert parse_posting("https://www.coinbase.com/careers/positions/8047150?gh_jid=8047150") == {
        "p": "greenhouse", "slug": None, "jid": "8047150",
    }
    assert parse_posting("https://jobs.lever.co/palantir/491a0133-b580-45c3-9fd4-2314faf82fb9")["p"] == "lever"
    assert parse_posting("https://jobs.ashbyhq.com/perplexity/f45ba174-7e99-4743-9d19-6e4d3e196458")["slug"] == "perplexity"
    yc = parse_posting("https://www.ycombinator.com/companies/asendia-ai/jobs/euK3IzX-product-engineer")
    assert yc == {"p": "ycombinator", "slug": "asendia-ai", "jid": "euK3IzX-product-engineer"}


def test_http_200_is_not_an_open_job():
    body = "<html><h1>Careers</h1><p>See all open roles.</p></html>"
    assert page_signal(200, body, "8107670003") == "closed"
    assert page_signal(200, "The job you are looking for is no longer open. id 8107670003", "8107670003") == "closed"
    assert page_signal(200, "Senior engineer 8107670003 " + ("responsibility " * 20), "8107670003") == "unknown"
    assert page_signal(404, "missing", "8107670003") == "closed"


def test_missing_from_board_is_closed_and_a_failed_read_is_not():
    gone = {"dh": "abc"}
    assert apply_check(gone, board_ok=True, present=False, live_hash=None) == "closed"
    assert gone["closed"] is True

    unread = {}
    assert apply_check(unread, board_ok=False, present=False, live_hash=None) == "unknown"
    assert "closed" not in unread


def test_description_change_keeps_the_original_hash():
    job = {"dh": "originalhash0000"}
    assert apply_check(job, board_ok=True, present=True, live_hash="originalhash0000") == "open"
    assert "chg" not in job
    assert apply_check(job, board_ok=True, present=True, live_hash="rewrittenhash000") == "changed"
    assert job["chg"] is True
    assert job["dh"] == "originalhash0000"


def test_a_matching_description_keeps_a_change_already_noted():
    job = {"dh": "originalhash0000", "chg": True}
    assert apply_check(job, board_ok=True, present=True, live_hash="originalhash0000") == "changed"
    assert job["chg"] is True
    assert job["dh"] == "originalhash0000"


def test_yc_listing_matches_the_job_url_tail():
    postings = [{"id": "euK3IzX", "url": "/companies/asendia-ai/jobs/euK3IzX-product-engineer"}]
    assert yc_listed(postings, "euK3IzX-product-engineer")
    assert not yc_listed(postings, "other-job")
