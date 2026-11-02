"""Inline web/ (css, js, data) into one self-contained HTML page for the hosted preview.

    python scripts/bundle.py dist/reedy.html
"""
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def main(out: str) -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    data = (WEB / "data" / "jobs.json").read_text(encoding="utf-8").replace("</", "<\\/")
    tech_path = WEB / "data" / "tech.json"
    tech = tech_path.read_text(encoding="utf-8").replace("</", "<\\/") if tech_path.exists() else '{"companies":{},"jobs":[]}'
    trad_path = WEB / "data" / "traditional.json"
    traditional = trad_path.read_text(encoding="utf-8").replace("</", "<\\/") if trad_path.exists() else '{"companies":{},"jobs":[]}'

    head = re.search(r"<title>.*?</title>", html, re.S).group(0)
    fonts = re.search(r'<link rel="stylesheet" href="https://fonts[^>]+>', html).group(0)
    body = re.search(r"<!--APP-->(.*)<!--DATA-->", html, re.S).group(1)
    page = (
        f'{head}\n<meta name="claude:viewport-fit-sides" content="cover">\n{fonts}\n<style>\n{css}\n</style>\n'
        f'{body}\n<script type="application/json" id="jobs-data">{data}</script>\n'
        f'<script type="application/json" id="tech-data">{tech}</script>\n'
        f'<script type="application/json" id="traditional-data">{traditional}</script>\n'
        f"<script>window.__BUNDLED__ = true;</script>\n<script>\n{js}\n</script>\n"
    )
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dist/reedy.html")
