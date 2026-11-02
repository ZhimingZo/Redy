# Redy

A free job board for **Mid-Senior and above** engineering and product roles. v0 covers **US startups backed by a16z or Y Combinator**, sourced from each company's own job board.

## How the data is collected

| Source | Discovery | Jobs |
| --- | --- | --- |
| a16z portfolio | `jobs.a16z.com/companies` (embedded company records: stage, headcount, domain) → each company's a16z page links to its ATS board | Ashby, Greenhouse and Lever public JSON APIs |
| Y Combinator | open `yc-oss` dataset of hiring companies | `ycombinator.com/companies/<slug>/jobs` (structured role, min experience, salary, visa) |

Then `crawler/normalize.py` keeps engineering and PM roles in the US, classifies level (`mid`, `senior`, `staff`, `mgmt`, `unk`; junior is dropped), years of experience, specialty, pay (annual USD) and visa sponsorship, and merges duplicates. Rules live in `crawler/classify.py`; `crawler/locations.py` cleans locations to one form ("San Francisco, CA", "Remote (US)") and drops non-US remote roles. Tests are in `tests/`.

## Run it

```bash
pip install -r requirements.txt
python -m pytest -q tests
python -m crawler                                  # live crawl -> web/data/jobs.json
python -m crawler --from-snapshots snapshots/      # rebuild from saved snapshots
python -m http.server -d web 8000                  # open http://localhost:8000
python scripts/bundle.py dist/redy.html           # single-file build for previews
```

## Deploy (free)

1. Push this folder to a GitHub repo.
2. `.github/workflows/crawl.yml` crawls every hour and commits `web/data/jobs.json`.
3. Import the repo in Vercel with **Root directory = `web`**, no build command. Every data commit redeploys.

## Layout

```
crawler/            __main__.py  classify.py  locations.py  normalize.py  sources/{ats,discovery}.py
tests/              classifier tests
web/                index.html  styles.css  app.js  data/jobs.json
scripts/bundle.py   inline everything into one HTML file
snapshots/          a16z.json  yc.json  (first crawl, Oct 31, 2026)
```

## Next iterations

- Tech and Traditional industry tracks: Workday, Eightfold, Oracle and custom career-site adapters.
- Postgres + accounts (schema in the PRD's ERD), tracker synced across devices.
- Email and phone push alerts for saved searches; AI mock interviews.
- LLM fallback for roles with no stated level (`unk`, ~17% of rows today).
