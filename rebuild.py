import json
from pathlib import Path

from template import build_entry, entry_filename, render_article, render_index, sort_entries

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
ARTICLES_DIR = BASE_DIR / "articles"
INDEX_FILE = BASE_DIR / "index.html"


def main():
	all_data = []
	for path in DATA_DIR.glob("*.json"):
		with open(path, encoding="utf-8") as f:
			all_data.append(json.load(f))

	entries = sort_entries(build_entry(d) for d in all_data)

	for data in all_data:
		out_path = ARTICLES_DIR / entry_filename(data)
		out_path.write_text(render_article(data, entries), encoding="utf-8")
		print(f"Rendered: {out_path}")

	INDEX_FILE.write_text(render_index(entries), encoding="utf-8")
	print(f"Rendered: {INDEX_FILE}")


if __name__ == "__main__":
	main()
