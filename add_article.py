"""Manually add one article without calling Gemini.

Two-step workflow so a human (or Claude) can supply the translation/vocab directly:

  python3 add_article.py scrape <url> <out_scraped.json>
      -> scrapes paragraphs + images, writes them for review/translation

  python3 add_article.py finalize <date> <source_name> <title> <url> <scraped.json> <translation.json>
      -> combines the scraped content with a translation.json shaped like:
         {"paragraphs": [{"index": 0, "translation": "..."}], "vocab": [...]}
         (same shape generate.py's call_gemini() returns), then writes
         data/{date}.json + articles/{date}.html and rebuilds index.html
"""
import json
import sys
from pathlib import Path

from generate import fetch_article_content, build_article_data, DATA_DIR, ARTICLES_DIR, INDEX_FILE
from template import render_article, render_index


def scrape(url, out_path):
	paragraphs, images = fetch_article_content(url)
	Path(out_path).write_text(
		json.dumps({"paragraphs": paragraphs, "images": images}, ensure_ascii=False, indent="\t"),
		encoding="utf-8",
	)
	print(f"Scraped {len(paragraphs)} paragraphs, {len(images)} images -> {out_path}")


def finalize(date_str, source_name, title, url, scraped_path, translation_path):
	scraped = json.loads(Path(scraped_path).read_text(encoding="utf-8"))
	gemini_data = json.loads(Path(translation_path).read_text(encoding="utf-8"))

	data = build_article_data(
		source_name, title, url, date_str, scraped["paragraphs"], scraped["images"], gemini_data
	)

	DATA_DIR.mkdir(exist_ok=True)
	data_path = DATA_DIR / f"{date_str}.json"
	data_path.write_text(json.dumps(data, ensure_ascii=False, indent="\t"), encoding="utf-8")
	print(f"Written: {data_path}")

	out_path = ARTICLES_DIR / f"{date_str}.html"
	out_path.write_text(render_article(data), encoding="utf-8")
	print(f"Written: {out_path}")

	entries = []
	for path in sorted(DATA_DIR.glob("*.json"), reverse=True):
		with open(path, encoding="utf-8") as f:
			d = json.load(f)
		entries.append({
			"date": d["date"],
			"title": d["title"],
			"source_name": d["source_name"],
			"filename": f"{d['date']}.html",
		})
	INDEX_FILE.write_text(render_index(entries), encoding="utf-8")
	print(f"Written: {INDEX_FILE}")


if __name__ == "__main__":
	cmd = sys.argv[1]
	if cmd == "scrape":
		scrape(sys.argv[2], sys.argv[3])
	elif cmd == "finalize":
		finalize(*sys.argv[2:8])
	else:
		print(f"Unknown command: {cmd}")
		sys.exit(1)
