import json

from crawler.refresh import (
    EIGHTFOLD,
    SKIP_DOMAINS,
    _registered,
    compose_board,
    enough,
    keep_pull,
    posting_places_for,
)
from crawler.normalize import note_changes
from crawler.sources.ats import greenhouse_posting_places


def test_empty_board_is_trusted_only_when_the_api_answered():
    assert keep_pull(0, 40, trust_empty=True)
    assert not keep_pull(0, 40, trust_empty=False)
    assert not keep_pull(9, 40, trust_empty=False)
    assert keep_pull(30, 40, trust_empty=False)


def test_a_collapsed_file_is_not_published():
    assert enough(80, 100)
    assert not enough(40, 100)
    assert not enough(0, 0)


def test_failed_company_stays_and_a_new_pull_replaces_the_other():
    old = {
        "generated_at": "yesterday",
        "companies": {
            "pinterest.com": {"n": "Pinterest", "bio": "Pins"},
            "kept.com": {"n": "Kept", "bio": "Still here"},
        },
        "jobs": [
            {"c": "pinterest.com", "t": "Old role", "at": "2026-01-01"},
            {"c": "kept.com", "t": "Stay", "at": "2026-02-01"},
        ],
    }
    fresh = {
        "generated_at": "today",
        "companies": {"pinterest.com": {"n": "Pinterest", "d": "pinterest.com"}},
        "jobs": [{"c": "pinterest.com", "t": "New role", "at": "2026-10-01"}],
    }
    doc = compose_board(old, fresh, {"pinterest.com"})
    assert [job["t"] for job in doc["jobs"]] == ["New role", "Stay"]
    assert doc["companies"]["pinterest.com"]["bio"] == "Pins"
    assert doc["companies"]["kept.com"]["bio"] == "Still here"
    assert doc["generated_at"] == "today"


def test_a_real_empty_board_drops_that_company_only():
    old = {
        "companies": {"gone.com": {"n": "Gone"}, "stay.com": {"n": "Stay"}},
        "jobs": [{"c": "gone.com", "t": "Old", "at": "2026-01-01"}, {"c": "stay.com", "t": "Stay", "at": "2026-01-02"}],
    }
    doc = compose_board(old, {"companies": {}, "jobs": [], "generated_at": "today"}, {"gone.com"})
    assert [job["c"] for job in doc["jobs"]] == ["stay.com"]
    assert "gone.com" not in doc["companies"]


def test_cloudflare_uses_the_posting_city_and_hp_is_not_the_sandbox():
    assert posting_places_for("cloudflare.com") is greenhouse_posting_places
    assert posting_places_for("pinterest.com") is None
    origins = {domain: origin for _name, origin, domain in EIGHTFOLD}
    assert origins["hp.com"] == "https://apply.hp.com"
    assert all("sandbox" not in origin for origin in origins.values())
    assert "analog.com" in SKIP_DOMAINS


def test_analog_devices_is_left_out_of_the_ashby_pull(tmp_path):
    path = tmp_path / "reg.json"
    path.write_text(json.dumps({"companies": [
        {"name": "Analog Devices", "domain": "analog.com", "ats": "ashby", "slug": "analog"},
        {"name": "Notion", "domain": "notion.com", "ats": "ashby", "slug": "notion"},
    ]}), encoding="utf-8")
    specs = _registered(str(path), "tech", {"ashby"}, set(), True)
    assert [domain for domain, _trust, _fn in specs] == ["notion.com"]


def test_replacing_a_company_marks_a_rewritten_description():
    old = {
        "companies": {"pinterest.com": {"n": "Pinterest"}, "kept.com": {"n": "Kept", "bio": "Stay"}},
        "jobs": [
            {"c": "pinterest.com", "id": "a", "t": "Role", "at": "2026-01-01", "dh": "before"},
            {"c": "kept.com", "id": "b", "t": "Stay", "at": "2026-01-01", "dh": "same", "chg": True},
        ],
    }
    fresh = {
        "generated_at": "today",
        "companies": {"pinterest.com": {"n": "Pinterest"}},
        "jobs": [{"c": "pinterest.com", "id": "a", "t": "Role", "at": "2026-10-27", "dh": "after"}],
    }
    doc = compose_board(old, fresh, {"pinterest.com"})
    pin = next(job for job in doc["jobs"] if job["c"] == "pinterest.com")
    kept = next(job for job in doc["jobs"] if job["c"] == "kept.com")
    assert pin["chg"] is True
    assert kept["chg"] is True


def test_an_unchanged_description_clears_the_flag():
    jobs = [{"id": "a", "dh": "same", "chg": True}]
    note_changes([{"id": "a", "dh": "same"}], jobs)
    assert "chg" not in jobs[0]
