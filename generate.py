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

from template import (
	FIXED_TAGS,
	SLOT_ORDER,
	build_entry,
	entry_filename,
	render_article,
	render_index,
	sort_entries,
)

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


def existing_slots():
	return {p.stem for p in ARTICLES_DIR.glob("*.html")}


def load_all_entries():
	entries = []
	for path in DATA_DIR.glob("*.json"):
		with open(path, encoding="utf-8") as f:
			entries.append(build_entry(json.load(f)))
	return sort_entries(entries)


def fetch_feed(rss_url):
	feed = feedparser.parse(rss_url)
	return feed.entries


def used_urls():
	urls = set()
	for path in DATA_DIR.glob("*.json"):
		with open(path, encoding="utf-8") as f:
			urls.add(json.load(f)["url"])
	return urls


def pick_article(entries, used):
	for entry in entries:
		if entry.get("link", "") not in used:
			return entry
	return None


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
	from google import genai
	client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

	article_text = "\n\n".join(
		f"[{i}] {p['text']}" for i, p in enumerate(paragraphs)
	)

	tag_list = "、".join(FIXED_TAGS)

	prompt = f"""你是一個英文學習助手。以下是一篇來自 {source_name} 的文章：
標題：{title}
網址：{url}

文章內容（每段前有段落編號）：
{article_text}

請完成以下任務，輸出 JSON（不要加 markdown code block）：

{{
  "tag": "從清單中選一個最符合這篇文章主題的分類",
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
      "pos": "詞性縮寫，如 n. / v. / adj. / adv. / prep. / conj.",
      "ipa": "/發音/",
      "definition_zh": "中文釋義（15字以內）",
      "example": "從文章中包含此詞的原句"
    }}
  ]
}}

規則：
- paragraphs 陣列長度必須與輸入段落數一致
- tag 必須是以下其中一個，不可自創：{tag_list}
- vocab 陣列請挑選共 20-24 個單字，比例約為：
  - type "highfreq"（常見但實用的詞，如動詞/形容詞/副詞）約 50%
  - type "general"（值得學習、但不算高頻也不算專業術語的單字）約 40%
  - type "term"（AI/科技/專業術語）約 10%
- 另外額外挑 2-4 個實用片語或慣用語，type 設為 "phrase"，pos 留空字串 ""
- 除了 type "phrase" 以外，每個單字都要標注 pos（詞性縮寫）
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


def build_article_data(source_name, title, url, date_str, slot, tag, paragraphs, images, gemini_data):
	translations = {p["index"]: p["translation"] for p in gemini_data["paragraphs"]}
	return {
		"date": date_str,
		"slot": slot,
		"tag": tag,
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


def main():
	DATA_DIR.mkdir(exist_ok=True)
	sources = load_sources()
	done = existing_slots()
	today = os.environ.get("DATE_OVERRIDE") or datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

	time_slot = os.environ.get("TIME_SLOT", "morning")
	if time_slot not in SLOT_ORDER:
		print(f"Unknown TIME_SLOT '{time_slot}', falling back to 'morning'.")
		time_slot = "morning"

	today_slot = f"{today}-{time_slot}"
	if today_slot in done:
		print(f"Article for {today_slot} already exists, skipping.")
		sys.exit(0)

	random.shuffle(sources)
	used = used_urls()
	selected_entry = None
	selected_source = None

	for source in sources:
		try:
			feed_entries = fetch_feed(source["rss"])
			entry = pick_article(feed_entries, used)
			if entry:
				selected_entry = entry
				selected_source = source
				break
		except Exception as e:
			print(f"Failed to fetch {source['name']}: {e}")
			continue

	if not selected_entry:
		print(f"No unused article found from any source for {today_slot}, skipping.")
		sys.exit(0)

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

	tag = gemini_data.get("tag")
	if tag not in FIXED_TAGS:
		print(f"Unexpected tag '{tag}' from Gemini, falling back to '科技'.")
		tag = "科技"

	data = build_article_data(source_name, title, url, today, time_slot, tag, paragraphs, images, gemini_data)

	data_path = DATA_DIR / f"{today_slot}.json"
	data_path.write_text(json.dumps(data, ensure_ascii=False, indent="\t"), encoding="utf-8")
	print(f"Written: {data_path}")

	entries = load_all_entries()

	out_path = ARTICLES_DIR / entry_filename(data)
	out_path.write_text(render_article(data, entries), encoding="utf-8")
	print(f"Written: {out_path}")

	INDEX_FILE.write_text(render_index(entries), encoding="utf-8")
	print("Done.")


if __name__ == "__main__":
	main()
