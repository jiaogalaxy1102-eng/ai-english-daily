# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A static site (GitHub Pages, see `.nojekyll`) that publishes one bilingual (English/Traditional Chinese) AI-news article per day, with interactive vocabulary popups. There is no build tool beyond the Python scripts below — HTML files in `articles/` and `index.html` are committed directly to the repo.

## Commands

```bash
pip install -r requirements.txt

# Generate today's article (requires GEMINI_API_KEY env var)
python generate.py

# Regenerate a specific date instead of today
DATE_OVERRIDE=2026-07-10 python generate.py

# Re-render ALL articles + index.html from data/*.json using the current template
# (run this after editing template.py, so old articles pick up the new design)
python rebuild.py
```

There is no test suite, linter, or build step.

## Architecture

Content and presentation are split into three stages:

1. **`generate.py`** — the daily pipeline. Picks an RSS source from `sources.json`, scrapes the article body (`fetch_article_content`, via BeautifulSoup), sends paragraphs to Gemini (`call_gemini`, model set by `GEMINI_MODEL`) for Traditional Chinese translation + vocab extraction, then assembles a plain dict (`build_article_data`) and writes it to `data/{date}.json`. It then calls into `template.py` to render `articles/{date}.html` and calls `rebuild_index()` to regenerate `index.html` from everything in `data/`.
2. **`data/{date}.json`** — the source of truth for a published article: title, source, url, `paragraphs` (original text + translation, in order), `images` (with an `after_paragraph` position), and `vocab` (word/type/ipa/definition/example). Nothing here is HTML-escaped or pre-highlighted — that's `template.py`'s job.
3. **`template.py`** — pure rendering. `render_article(data)` turns one article dict into a full HTML page (word-highlighting via `highlight()`, vocab popup markup + inline JS, image interleaving). `render_index(entries)` builds `index.html` from a list of `{date, title, source_name, filename}`. Both are called from `generate.py` (daily, incremental) and from `rebuild.py` (bulk, reads every file in `data/`).

**To change the site's design or article layout**: edit `template.py` (and `style.css`), then run `python rebuild.py` to re-apply it to every existing article, not just future ones. There's also a manual `Rebuild Site` GitHub Actions workflow (`.github/workflows/rebuild.yml`) that does the same in CI.

### Things that aren't obvious from one file alone

- **`after_paragraph` indexing**: in `data/*.json`, an image's `after_paragraph` value equals "how many paragraphs had already been scraped when this image was encountered" (1-indexed count, set in `fetch_article_content`). `render_article` consumes it as `images_by_para.get(i + 1, [])` inside the `enumerate(paragraphs)` loop — the `+1` is required to line up the scraper's counting convention with the renderer's 0-indexed loop. Keep both sides in sync if either changes.
- **CSS cache-busting**: `template.py` has a single `CSS_VERSION` constant used for both `articles/*.html` (`../style.css?v=N`) and `index.html` (`style.css?v=N`). Bump it whenever `style.css` changes so browsers don't serve a stale cached copy — both templates must stay on the same version.
- **Idempotency / "already done" check**: `generate.py`'s `existing_dates()` globs `articles/*.html` (not `data/*.json`) to decide whether today's article was already generated. `data/` and `articles/` are expected to always be written together — don't let one advance without the other, or the daily job will silently regenerate (or silently skip) a day.
- **`.github/workflows/daily.yml`** runs on a schedule (cron, UTC 01:00 = Taiwan 09:00) and via manual `workflow_dispatch` with an optional `date_override` input; it commits `data/`, `articles/`, and `index.html` together after running `generate.py`.
- Vocab words are highlighted in paragraph text by wrapping the first case-insensitive match per word/phrase in a `<span class="word-highfreq">` or `<span class="word-term">` (see `highlight()` in `template.py`); longer phrases are matched before shorter ones (sorted by length descending) to avoid a short word inside a longer phrase stealing the match.
