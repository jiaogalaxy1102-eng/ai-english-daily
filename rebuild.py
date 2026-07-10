import json
from pathlib import Path

from template import render_article, render_index

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
ARTICLES_DIR = BASE_DIR / "articles"
INDEX_FILE = BASE_DIR / "index.html"


def main():
	entries = []
	for path in sorted(DATA_DIR.glob("*.json"), reverse=True):
		with open(path, encoding="utf-8") as f:
			data = json.load(f)

		out_path = ARTICLES_DIR / f"{data['date']}.html"
		out_path.write_text(render_article(data), encoding="utf-8")
		print(f"Rendered: {out_path}")

		entries.append({
			"date": data["date"],
			"title": data["title"],
			"source_name": data["source_name"],
			"filename": f"{data['date']}.html",
		})

	INDEX_FILE.write_text(render_index(entries), encoding="utf-8")
	print(f"Rendered: {INDEX_FILE}")


if __name__ == "__main__":
	main()
