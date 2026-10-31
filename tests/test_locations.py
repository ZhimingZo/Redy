import pytest

from crawler.locations import pretty_locations as P


@pytest.mark.parametrize("raw,locs,remote", [
    (["San Francisco, California, United States"], ["San Francisco, CA"], False),
    (["San Francisco"], ["San Francisco, CA"], False),
    (["NYC HQ"], ["New York, NY"], False),
    (["New York City (Hybrid)"], ["New York, NY"], False),
    (["US - Remote"], ["Remote (US)"], True),
    (["United States (Remote)"], ["Remote (US)"], True),
    (["US", "Remote (US)"], ["Remote (US)"], True),
    (["San Francisco, CA, US / Remote (US)"], ["San Francisco, CA"], True),
    (["Remote (San Francisco, CA, US; New York, NY, US)"], ["San Francisco, CA", "New York, NY"], True),
    (["HQ - Sunnyvale (Office)"], ["Sunnyvale, CA"], False),
    (["Credal HQ (Brooklyn, NY)"], ["Brooklyn, NY"], False),
    (["NY, SF or Remote / Remote (US)"], ["New York, NY", "San Francisco, CA"], True),
    (["901 Market Floor 6, SF, CA 94105"], ["San Francisco, CA"], False),
    (["Long Beach CA"], ["Long Beach, CA"], False),
    (["San Francisco, CA, New York, NY, Portland, OR, or Remote within Canada or United States"],
     ["San Francisco, CA", "New York, NY", "Portland, OR"], False),
    (["Kent, Washington"], ["Kent, WA"], False),
    (["Washington, D.C."], ["Washington, DC"], False),
    (["Franklin, LA"], ["Franklin, LA"], False),
    (["Indianapolis, IN, US"], ["Indianapolis, IN"], False),
    (["San Francisco Bay Area, CA"], ["SF Bay Area"], False),
    (["Boston Area"], ["Boston, MA"], False),
    (["CA, US / Remote (CA, US)"], ["California"], True),
    (["AMER - US - Remote"], ["Remote (US)"], True),
    (["SF Headquarters"], ["San Francisco, CA"], False),
    (["West Virginia"], ["West Virginia"], False),
])
def test_pretty(raw, locs, remote):
    assert P(raw) == (locs, remote)


@pytest.mark.parametrize("raw", [
    ["Remote (IN)"], ["IN / Remote (IN)"], ["Toronto, ON, CA"], ["Bengaluru, KA, IN / Bengaluru, Karnataka, IN"],
    ["Remote (CA)"], ["Delhi, IN"], ["Remote (Toronto, ON, CA)"], ["Amsterdam, NH, NL"],
])
def test_non_us_dropped(raw):
    assert P(raw) == ([], False)


def test_non_us_remote_does_not_add_remote():
    assert P(["San Francisco, CA, US / Remote (IN)"]) == (["San Francisco, CA"], False)
