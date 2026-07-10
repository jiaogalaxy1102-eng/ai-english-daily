import json
import random
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import time
import feedparser
import requests
from bs4 import BeautifulSoup
from google import genai

from template import render_article, render_index

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-3-flash-preview"

BASE_DIR = Path(__file__).parent
ARTICLES_DIR = BASE_DIR / "articles"
DATA_DIR = BASE_DIR / "data"
SOURCES_FILE = BASE_DIR / "sources.json"
INDEX_FILE = BASE_DIR / "index.html"

MAX_PARAGRAPHS = 25
MAX_CHARS = 15000


def load_sources():
	with open(SOURCES_FILE, encoding="utf-8") as f:
		return json.load(f)


def existing_dates():
	return {p.stem for p in ARTICLES_DIR.glob("*.html")}


def fetch_feed(rss_url):
	feed = feedparser.parse(rss_url)
	return feed.entries


def pick_article(entries, done_dates):
	random.shuffle(entries)
	for entry in entries:
		date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
		if date_str not in done_dates:
			return entry
	return entries[0] if entries else None


def fetch_article_content(url):
	headers = {"User-Agent": "Mozilla/5.0"}
	resp = requests.get(url, headers=headers, timeout=15)
	resp.raise_for_status()
	soup = BeautifulSoup(resp.text, "lxml")

	# remove nav, header, footer, aside, script, style
	for tag in soup(["nav", "header", "footer", "aside", "script", "style", "form"]):
		tag.decompose()

	# find main content area
	content = (
		soup.find("article")
		or soup.find("main")
		or soup.find(class_=re.compile(r"post|article|content|entry", re.I))
		or soup.find("body")
	)

	paragraphs = []
	images = []
	char_count = 0

	def is_avatar(img_tag, src, alt):
		src_l = src.lower()
		if any(k in src_l for k in ["avatar", "profile", "/user/", "/users/", "contributor", "headshot", "author"]):
			return True
		alt_l = alt.lower()
		if any(k in alt_l for k in ["avatar", "profile photo", "author photo", "headshot"]):
			return True
		try:
			w = int(img_tag.get("width", 0))
			h = int(img_tag.get("height", 0))
			if 0 < w < 200 and 0 < h < 200:
				return True
		except (ValueError, TypeError):
			pass
		for parent in img_tag.parents:
			if parent.name in ["html", "body"]:
				break
			combined = " ".join(parent.get("class", [])) + " " + (parent.get("id", ""))
			if any(k in combined.lower() for k in ["author", "avatar", "bio", "contributor", "sidebar", "profile"]):
				return True
		return False

	for elem in content.find_all(["p", "h2", "h3", "img"], recursive=True):
		if elem.name == "img":
			src = elem.get("src", "")
			alt = elem.get("alt", "")
			if src and not src.startswith("data:"):
				if src.startswith("//"):
					src = "https:" + src
				elif src.startswith("/"):
					from urllib.parse import urlparse
					parsed = urlparse(url)
					src = f"{parsed.scheme}://{parsed.netloc}{src}"
				if not is_avatar(elem, src, alt):
					images.append({"src": src, "alt": alt, "after_paragraph": len(paragraphs)})
		elif elem.name in ["p", "h2", "h3"]:
			text = elem.get_text(strip=True)
			if len(text) < 20:
				continue
			paragraphs.append({"text": text, "tag": elem.name})
			char_count += len(text)
			if len(paragraphs) >= MAX_PARAGRAPHS or char_count >= MAX_CHARS:
				break

	return paragraphs, images


def call_gemini(source_name, title, url, paragraphs):
	article_text = "\n\n".join(
		f"[{i}] {p['text']}" for i, p in enumerate(paragraphs)
	)

	prompt = f"""你是一個英文學習助手。以下是一篇來自 {source_name} 的文章：
標題：{title}
網址：{url}

文章內容（每段前有段落編號）：
{article_text}

請完成以下任務，輸出 JSON（不要加 markdown code block）：

{{
  "paragraphs": [
    {{
      "index": 0,
      "translation": "第0段的繁體中文翻譯"
    }}
  ],
  "vocab": [
    {{
      "word": "英文單字或片語",
      "type": "highfreq",
      "ipa": "/發音/",
      "definition_zh": "中文釋義（15字以內）",
      "example": "從文章中包含此詞的原句"
    }}
  ]
}}

規則：
- paragraphs 陣列長度必須與輸入段落數一致
- vocab 挑出 8-12 個值得學習的單字/片語：
  - type "highfreq"：常見但實用的詞（動詞、形容詞、副詞等）
  - type "term"：AI/技術/專業術語
- ipa 使用標準 IPA
- 只輸出 JSON，不加任何說明"""

	for attempt in range(3):
		try:
			response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
			text = response.text.strip()
			text = re.sub(r"^```json\s*", "", text)
			text = re.sub(r"\s*```$", "", text)
			return json.loads(text)
		except Exception as e:
			err = str(e)
			if ("429" in err or "503" in err) and attempt < 2:
				wait = 40 * (attempt + 1)
				print(f"Transient error ({err[:20]}...), retrying in {wait}s...")
				time.sleep(wait)
			else:
				raise


def build_article_data(source_name, title, url, date_str, paragraphs, images, gemini_data):
	translations = {p["index"]: p["translation"] for p in gemini_data["paragraphs"]}
	return {
		"date": date_str,
		"title": title,
		"source_name": source_name,
		"url": url,
		"paragraphs": [
			{"tag": p["tag"], "text": p["text"], "translation": translations.get(i, "")}
			for i, p in enumerate(paragraphs)
		],
		"images": images,
		"vocab": gemini_data["vocab"],
	}


def rebuild_index():
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


def main():
	DATA_DIR.mkdir(exist_ok=True)
	sources = load_sources()
	done = existing_dates()
	today = os.environ.get("DATE_OVERRIDE") or datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

	if today in done:
		print(f"Article for {today} already exists, skipping.")
		sys.exit(0)

	random.shuffle(sources)
	selected_entry = None
	selected_source = None

	for source in sources:
		try:
			entries = fetch_feed(source["rss"])
			if entries:
				selected_entry = entries[0]
				selected_source = source
				break
		except Exception as e:
			print(f"Failed to fetch {source['name']}: {e}")
			continue

	if not selected_entry:
		print("No article found from any source.")
		sys.exit(1)

	title = selected_entry.get("title", "Untitled")
	url = selected_entry.get("link", "")
	source_name = selected_source["name"]

	print(f"Processing: {title} ({source_name})")

	try:
		paragraphs, images = fetch_article_content(url)
	except Exception as e:
		print(f"Failed to fetch article content: {e}")
		sys.exit(1)

	if not paragraphs:
		print("No paragraphs extracted.")
		sys.exit(1)

	try:
		gemini_data = call_gemini(source_name, title, url, paragraphs)
	except Exception as e:
		print(f"Gemini error: {e}")
		sys.exit(1)

	data = build_article_data(source_name, title, url, today, paragraphs, images, gemini_data)

	data_path = DATA_DIR / f"{today}.json"
	data_path.write_text(json.dumps(data, ensure_ascii=False, indent="\t"), encoding="utf-8")
	print(f"Written: {data_path}")

	filename = f"{today}.html"
	out_path = ARTICLES_DIR / filename
	out_path.write_text(render_article(data), encoding="utf-8")
	print(f"Written: {out_path}")

	rebuild_index()
	print("Done.")


if __name__ == "__main__":
	main()
