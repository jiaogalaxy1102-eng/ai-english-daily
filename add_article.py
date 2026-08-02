"""手動新增或重跑一篇文章，不呼叫 Gemini。

拆成兩步，讓人（或 Claude）自己補翻譯/詞彙/測驗：

  python3 add_article.py scrape <url> <out_scraped.json>
      -> 爬出段落與圖片，寫成檔案供檢查與翻譯

  python3 add_article.py finalize <date> <tag> <source_name> <title> <url> <scraped.json> <content.json>
      -> content.json 的形狀跟 generate.py 的 call_gemini() 回傳值一樣：
         {
           "summary_en": "...", "summary_zh": "...", "conclusion_index": 0,
           "scan_questions": [{"question": "...", "answer_zh": "...", "paragraph_index": 0}],
           "paragraphs": [{"index": 0, "translation": "..."}],
           "vocab": [...], "quiz": [...]
         }
         然後寫出 data/{date}.json + articles/{date}.html 並重建 index.html / vocab.html
"""
import json
import sys
from pathlib import Path

from generate import (
	ARTICLES_DIR,
	DATA_DIR,
	INDEX_FILE,
	VOCAB_FILE,
	build_article_data,
	fetch_article_content,
	load_all_entries,
	rss_fulltext_html,
)
from template import entry_filename, render_article, render_index, render_vocab_page


def scrape(url, out_path):
	paragraphs, images = fetch_article_content(url)
	Path(out_path).write_text(
		json.dumps({"paragraphs": paragraphs, "images": images}, ensure_ascii=False, indent="\t"),
		encoding="utf-8",
	)
	print(f"Scraped {len(paragraphs)} paragraphs, {len(images)} images -> {out_path}")


def finalize(date_str, tag, source_name, title, url, scraped_path, content_path):
	scraped = json.loads(Path(scraped_path).read_text(encoding="utf-8"))
	content = json.loads(Path(content_path).read_text(encoding="utf-8"))

	data = build_article_data(
		source_name, title, url, date_str, tag,
		scraped["paragraphs"], scraped["images"], content,
	)

	DATA_DIR.mkdir(exist_ok=True)
	ARTICLES_DIR.mkdir(exist_ok=True)
	data_path = DATA_DIR / f"{date_str}.json"
	data_path.write_text(json.dumps(data, ensure_ascii=False, indent="\t"), encoding="utf-8")
	print(f"Written: {data_path}")

	entries = load_all_entries()
	out_path = ARTICLES_DIR / entry_filename(data)
	out_path.write_text(render_article(data, entries), encoding="utf-8")
	print(f"Written: {out_path}")

	INDEX_FILE.write_text(render_index(entries), encoding="utf-8")
	VOCAB_FILE.write_text(render_vocab_page(), encoding="utf-8")
	print(f"Written: {INDEX_FILE}, {VOCAB_FILE}")


if __name__ == "__main__":
	cmd = sys.argv[1]
	if cmd == "scrape":
		scrape(sys.argv[2], sys.argv[3])
	elif cmd == "finalize":
		finalize(*sys.argv[2:9])
	else:
		print(f"Unknown command: {cmd}")
		sys.exit(1)
