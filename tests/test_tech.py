from datetime import datetime, timezone

from crawler.locations import pretty_locations
from crawler.normalize import build
from crawler.sources.ats import greenhouse_posted, greenhouse_posting_places
from crawler.sources.careers import career_links, classify_board, names_match, slug_from_domain
from crawler.sources.discovery import company_domain, without_public
from crawler.sources.techlist import clean_wikidata, select_public, select_unicorns


def test_listed_companies_stay_off_the_startup_crawl():
    public = {"doordash.com"}
    rows = [
        {"name": "DoorDash", "website": "https://www.doordash.com/"},
        {"name": "A Startup", "domain": "example.com"},
    ]
    assert company_domain(rows[0]) == "doordash.com"
    assert [c["name"] for c in without_public(rows, public)] == ["A Startup"]


def test_classify_known_boards():
    assert classify_board("https://boards.greenhouse.io/cloudflare") == {"ats": "greenhouse", "slug": "cloudflare"}
    assert classify_board("https://job-boards.greenhouse.io/stripe") == {"ats": "greenhouse", "slug": "stripe"}
    assert classify_board("", '<iframe src="https://boards.greenhouse.io/embed/job_board?for=notion"></iframe>') == {"ats": "greenhouse", "slug": "notion"}
    assert classify_board("https://jobs.lever.co/spotify")["slug"] == "spotify"
    assert classify_board("https://jobs.ashbyhq.com/openai")["ats"] == "ashby"
    hit = classify_board("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
    assert hit["ats"] == "workday"
    assert hit["slug"] == "nvidia.wd5.myworkdayjobs.com"
    assert classify_board("https://example.com/about") is None
    assert classify_board("https://boards.greenhouse.io/embed") is None


def test_career_links_skip_social():
    html = '''
      <a href="/company">About</a>
      <a href="/careers">Careers</a>
      <a href="https://www.linkedin.com/jobs/nvidia">Jobs</a>
      <a href="https://jobs.example.com/openings">Openings</a>
    '''
    links = career_links(html, "https://www.example.com/home")
    assert links == ["https://www.example.com/careers", "https://jobs.example.com/openings"]


def test_domain_slugs():
    assert slug_from_domain("nvidia.com") == "nvidia"
    assert slug_from_domain("www.sony.co.in") == "sony"
    assert slug_from_domain("about.google") == "google"
    assert slug_from_domain("holdings.panasonic") == "panasonic"


def test_names_match_initials_and_tokens():
    assert names_match("Hewlett-Packard", "HP")
    assert names_match("Meta Platforms", "Meta")
    assert names_match("Cloudflare", "Cloudflare, Inc.")
    assert not names_match("Adobe", "Oracle")


def test_tech_source_is_not_labeled_a16z():
    raw = {
        "co": "NVIDIA", "cslug": "nvidia", "dom": "nvidia.com", "src": "tech", "p": "greenhouse",
        "id": "1", "t": "Senior Software Engineer", "loc": ["Santa Clara, CA"],
        "url": "https://example.com/1", "at": "2026-10-01T00:00:00Z",
        "yoe": ["5+ years of software experience"],
    }
    data = build([raw], datetime(2026, 10, 31, tzinfo=timezone.utc))
    assert data["companies"]["nvidia.com"]["backers"] == []
    assert data["companies"]["nvidia.com"]["n"] == "NVIDIA"
    assert len(data["jobs"]) == 1


def test_public_list_is_us_tech_only():
    rows = [
        {"Symbol": "NVDA", "Security": "Nvidia", "GICS Sector": "Information Technology",
         "GICS Sub-Industry": "Semiconductors", "Headquarters Location": "Santa Clara, California"},
        {"Symbol": "ACN", "Security": "Accenture", "GICS Sector": "Information Technology",
         "GICS Sub-Industry": "IT Consulting & Other Services", "Headquarters Location": "Dublin, Ireland"},
        {"Symbol": "AMZN", "Security": "Amazon", "GICS Sector": "Consumer Discretionary",
         "GICS Sub-Industry": "Broadline Retail", "Headquarters Location": "Seattle, Washington"},
        {"Symbol": "DIS", "Security": "Walt Disney", "GICS Sector": "Communication Services",
         "GICS Sub-Industry": "Movies & Entertainment", "Headquarters Location": "Burbank, California"},
        {"Symbol": "GOOGL", "Security": "Alphabet Inc. (Class A)", "GICS Sector": "Communication Services",
         "GICS Sub-Industry": "Interactive Media & Services", "Headquarters Location": "Mountain View, California"},
        {"Symbol": "GOOG", "Security": "Alphabet Inc. (Class C)", "GICS Sector": "Communication Services",
         "GICS Sub-Industry": "Interactive Media & Services", "Headquarters Location": "Mountain View, California"},
        {"Symbol": "T", "Security": "AT&T", "GICS Sector": "Communication Services",
         "GICS Sub-Industry": "Integrated Telecommunication Services", "Headquarters Location": "Dallas, Texas"},
        {"Symbol": "CART", "Security": "Instacart", "GICS Sector": "Consumer Staples",
         "GICS Sub-Industry": "Food Retail", "Headquarters Location": "San Francisco, California"},
    ]
    names = [c["name"] for c in select_public(rows)]
    assert names == ["Nvidia", "Accenture", "Amazon", "Alphabet Inc.", "Instacart"]


def test_unicorns_are_us_and_above_the_floor():
    rows = [
        {"name": "Stripe", "valuation": 159, "country": "United States", "industry": "Financial Services"},
        {"name": "ByteDance", "valuation": 480, "country": "China", "industry": "Media & Entertainment"},
        {"name": "TikTok USDS", "valuation": 14, "country": "United States", "industry": "Media & Entertainment"},
        {"name": "Revolut", "valuation": 75, "country": "United Kingdom", "industry": "Financial Services"},
        {"name": "CodeRabbit", "valuation": 1.5, "country": "United States", "industry": "Enterprise Tech"},
        {"name": "Anduril", "valuation": 14, "country": "United States", "industry": "Industrials"},
        {"name": "The Boring Company", "valuation": 23, "country": "United States", "industry": "Industrials"},
        {"name": "Chobani", "valuation": 16, "country": "United States", "industry": "Consumer & Retail"},
    ]
    names = [c["name"] for c in select_unicorns(rows)]
    assert names == ["Stripe", "Anduril"]


def test_clean_wikidata_drops_junk_and_ranks_fame():
    def row(name, site, sl, emp=None):
        r = {"companyLabel": {"value": name}, "website": {"value": site}, "sitelinks": {"value": str(sl)}}
        if emp:
            r["employees"] = {"value": str(emp)}
        return r

    rows = [
        row("Google", "https://about.google", 200, 1000),
        row("Google", "https://www.google.com", 200, 1000),
        row("Q12345", "https://sega.com", 70),
        row("State University", "https://state.edu", 80),
        row("Epic Records", "https://epic-records.example", 40),
        row("Small Studio", "https://small.example", 12),
        row("NVIDIA", "https://www.nvidia.com", 90, 42000),
    ]
    out = clean_wikidata(rows, limit=10)
    names = [c["name"] for c in out]
    assert names[0] == "Google"
    assert out[0]["domain"] == "google.com"
    assert "NVIDIA" in names
    assert "Sega" in names
    assert "State University" not in names
    assert "Epic Records" not in names


def test_cloudflare_page_reads_posting_location_not_the_word_distributed():
    job = {
        "location": {"name": "Distributed"},
        "offices": [{"name": "Toronto, ON"}],
        "metadata": [{"name": "Job Posting Location", "value": ["Austin, TX", "Toronto, Canada"]}],
    }
    places = greenhouse_posting_places(job)
    assert places == ["Austin, TX", "Toronto, Canada"]
    assert pretty_locations(places)[0] == ["Austin, TX"]


def test_greenhouse_date_is_the_last_change():
    assert greenhouse_posted({
        "first_published": "2024-02-22T16:37:49-05:00",
        "updated_at": "2026-10-27T18:38:50-04:00",
    }) == "2026-10-27T18:38:50-04:00"
    assert greenhouse_posted({"first_published": "2026-10-01T00:00:00Z"}) == "2026-10-01T00:00:00Z"
