"""Manually add or regenerate an article without calling Gemini.

Two-step workflow so a human (or Claude) can supply the translation/vocab/quiz directly:

  python3 add_article.py scrape <url> <out_scraped.json>
      -> scrapes paragraphs + images, writes them for review/translation

  python3 add_article.py finalize <date> <slot> <tag> <source_name> <title> <url> <scraped.json> <translation.json>
      -> slot is one of morning/noon/evening, or "-" for legacy single-slot articles
      -> combines the scraped content with a translation.json shaped like:
         {"paragraphs": [{"index": 0, "translation": "..."}], "vocab": [...], "quiz": [...]}
         (same shape generate.py's call_gemini() returns), then writes
         data/{date}[-slot].json + articles/{date}[-slot].html and rebuilds index.html
"""
import json
import sys
from pathlib import Path

from generate import build_article_data, fetch_article_content, load_all_entries, DATA_DIR, ARTICLES_DIR, INDEX_FILE
from template import entry_filename, render_article, render_index


def scrape(url, out_path):
	paragraphs, images = fetch_article_content(url)
	Path(out_path).write_text(
		json.dumps({"paragraphs": paragraphs, "images": images}, ensure_ascii=False, indent="\t"),
		encoding="utf-8",
	)
	print(f"Scraped {len(paragraphs)} paragraphs, {len(images)} images -> {out_path}")


def finalize(date_str, slot_arg, tag, source_name, title, url, scraped_path, translation_path):
	slot = None if slot_arg == "-" else slot_arg
	scraped = json.loads(Path(scraped_path).read_text(encoding="utf-8"))
	gemini_data = json.loads(Path(translation_path).read_text(encoding="utf-8"))

	data = build_article_data(
		source_name, title, url, date_str, slot, tag,
		scraped["paragraphs"], scraped["images"], gemini_data,
	)

	DATA_DIR.mkdir(exist_ok=True)
	slot_suffix = f"-{slot}" if slot else ""
	data_path = DATA_DIR / f"{date_str}{slot_suffix}.json"
	data_path.write_text(json.dumps(data, ensure_ascii=False, indent="\t"), encoding="utf-8")
	print(f"Written: {data_path}")

	entries = load_all_entries()
	out_path = ARTICLES_DIR / entry_filename(data)
	out_path.write_text(render_article(data, entries), encoding="utf-8")
	print(f"Written: {out_path}")

	INDEX_FILE.write_text(render_index(entries), encoding="utf-8")
	print(f"Written: {INDEX_FILE}")


if __name__ == "__main__":
	cmd = sys.argv[1]
	if cmd == "scrape":
		scrape(sys.argv[2], sys.argv[3])
	elif cmd == "finalize":
		finalize(*sys.argv[2:10])
	else:
		print(f"Unknown command: {cmd}")
		sys.exit(1)
