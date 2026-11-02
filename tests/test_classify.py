import pytest

from crawler import classify as C


@pytest.mark.parametrize("title,yoe,ngo,expected", [
    ("Senior Software Engineer, Payments", None, False, "senior"),
    ("Sr. Backend Engineer", None, False, "senior"),
    ("Staff Machine Learning Engineer", None, False, "staff"),
    ("Principal Product Manager", None, False, "staff"),
    ("Engineering Manager, Infrastructure", None, False, "mgmt"),
    ("Head of Product", None, False, "mgmt"),
    ("VP of Engineering", None, False, "mgmt"),
    ("Group Product Manager", None, False, "mgmt"),
    ("Software Engineer, New Grad", None, False, "junior"),
    ("Software Engineer Intern", None, False, "junior"),
    ("Associate Product Manager", None, False, "junior"),
    ("Software Engineer II", None, False, "junior"),
    ("Software Engineer III", None, False, "senior"),
    ("Founding Engineer", None, False, "senior"),
    ("Software Engineer", 6, False, "senior"),
    ("Software Engineer", 3, False, "mid"),
    ("Software Engineer", 1, False, "junior"),
    ("Software Engineer", 0, True, "junior"),
    ("Member of Technical Staff", None, False, "unk"),
    ("Member of Technical Staff, Inference", 6, False, "senior"),
    ("Product Manager", None, False, "unk"),
    ("Technical Lead Manager, Sensing Systems Design & Validation", None, False, "mgmt"),
    ("Applied ML/AI Manager - Behavior Validation", None, False, "mgmt"),
])
def test_level(title, yoe, ngo, expected):
    assert C.classify_level(title, yoe, ngo) == expected


@pytest.mark.parametrize("title,fam", [
    ("Senior Software Engineer", "eng"),
    ("Staff ML Researcher", "eng"),
    ("Product Manager, Growth", "pm"),
    ("Senior Product Manager - AI Platform", "pm"),
    ("Head of Product", "pm"),
    ("Product Designer", None),
    ("Product Marketing Manager", None),
    ("Solutions Engineer", None),
    ("Sales Engineer", None),
    ("Technical Recruiter", None),
    ("Product Engineer", "eng"),
    ("Forward Deployed Engineer", "eng"),
    ("Data Analyst", None),
    ("Data Scientist", "eng"),
    ("Technical Lead Manager, Sensing Systems Design & Validation", "eng"),
    ("Applied ML/AI Manager - Behavior Validation", "eng"),
    ("Senior Machine Learning Engineer – Perception & Embodied AI", "eng"),
    ("Software Engineer, Embodied Intelligence", "eng"),
])
def test_family(title, fam):
    assert C.classify_family(title) == fam


def test_member_of_technical_staff_is_eng():
    assert C.classify_family("Member of Technical Staff") == "eng"


def test_embodied_titles_are_ai():
    for title in (
        "Software Engineer, Embodied Intelligence",
        "Embodied AI Researcher",
        "Perception Engineer - Multimodal & Embodied AI",
    ):
        assert C.classify_family(title) == "eng"
        assert C.classify_subfamily(title, "eng") == "AI / ML"
    assert C.classify_family("Embodied AI") is None


@pytest.mark.parametrize("snips,yoe", [
    (["At least 5 years of experience building distributed systems"], 5),
    (["3-5 years of experience in software", "7+ years of professional experience"], 7),
    (["Less than 5 years since previously active TS/SCI clearance"], None),
    (["4-year vesting schedule"], None),
    ([], None),
])
def test_yoe(snips, yoe):
    assert C.parse_yoe(snips) == yoe


def test_yc_experience():
    assert C.parse_yc_experience("6+ years") == (6, False)
    assert C.parse_yc_experience("Any (new grads ok)") == (0, True)
    assert C.parse_yc_experience(None) == (None, False)


@pytest.mark.parametrize("locs,expected", [
    (["San Francisco, CA"], ["San Francisco, CA"]),
    (["New York, NY, US / Remote (US)"], ["New York, NY, US", "Remote (US)"]),
    (["London, UK"], []),
    (["Remote - Canada"], []),
    (["Remote"], ["Remote"]),
    (["Toronto, ON, Canada", "Seattle, WA"], ["Seattle, WA"]),
    (["Bengaluru, KA, IN"], []),
    (["Indianapolis, IN"], ["Indianapolis, IN"]),
    (["Austin, TX, US"], ["Austin, TX, US"]),
])
def test_us_locations(locs, expected):
    assert C.us_locations(locs) == expected


def test_pay():
    assert C.parse_yc_salary("$180K - $230K") == [180000, 230000]
    assert C.parse_yc_salary("$170K") == [170000, 170000]
    assert C.parse_yc_salary("") is None
    assert C.normalize_pay(80, 100, "USD", "1 HOUR") == [166000, 208000]
    assert C.normalize_pay(100000, 150000, "EUR", "year") is None


def test_days_from_ago():
    assert C.days_from_ago("1 day") == 1
    assert C.days_from_ago("about 2 months") == 60
    assert C.days_from_ago("over 1 year") == 419
    assert C.days_from_ago("3 hours") == 0


def test_stage_and_batch():
    assert C.stage_bucket("Series B", "a16z") == "Series A–B"
    assert C.stage_bucket("Series E", "a16z") == "Series C+"
    assert C.stage_bucket("Early", "YC") == "Seed / Early"
    assert C.short_batch("Winter 2012") == "W12"
    assert C.short_batch("Fall 2025") == "F25"
