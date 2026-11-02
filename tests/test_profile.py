from crawler.sources.discovery import profile_from_yc


def test_ceo_year_and_one_liner():
    prof = profile_from_yc({
        "one_liner": "Economic infrastructure for the internet.",
        "year_founded": 2009,
        "founders": [
            {"full_name": "John Collison", "title": "President"},
            {"full_name": "Patrick Collison", "title": "Founder/CEO"},
        ],
    })
    assert prof == {
        "bio": "Economic infrastructure for the internet.",
        "yr": 2009,
        "ceo": "Patrick Collison",
    }


def test_founders_when_nobody_is_ceo():
    prof = profile_from_yc({
        "one_liner": "Factory robotics",
        "year_founded": "2018",
        "founders": [
            {"full_name": "Ada Lovelace", "title": "Founder"},
            {"full_name": "Grace Hopper", "title": "CTO"},
            {"full_name": "Extra Person", "title": "Founder"},
        ],
    })
    assert prof["yr"] == 2018
    assert prof["founders"] == "Ada Lovelace, Founder · Grace Hopper, CTO"
    assert "ceo" not in prof


def test_empty_company():
    assert profile_from_yc({}) == {}
    assert profile_from_yc(None) == {}
