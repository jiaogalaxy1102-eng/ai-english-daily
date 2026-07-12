import html
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
from bs4 import BeautifulSoup, NavigableString

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

MAX_PARAGRAPHS = 80
MAX_CHARS = 45000


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

	# remove native ad / sponsored content blocks (e.g. Vox Media's data-native-ad-id containers)
	for tag in soup.select('[data-native-ad-id], [class*="native-ad"], [class*="sponsor"], [class*="advertisement"]'):
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
	seen_texts = set()
	seen_srcs = set()

	# a small safe set of inline tags we preserve as real HTML when building
	# paragraph text (converted to their canonical form); everything else is
	# unwrapped to plain text. Paragraph text is embedded unescaped downstream
	# (see template.py), so raw text content must be escaped here.
	INLINE_TAG_MAP = {"strong": "strong", "b": "strong", "em": "em", "i": "em", "mark": "strong", "code": "code"}

	def _inline_parts(elem):
		# recursive collector — never strips, so whitespace at any nesting
		# depth survives; only the top-level extract_inline() below collapses
		# it once. Stripping per recursive call would eat the same boundary
		# space this whole approach exists to preserve — see NOTES.md
		parts = []
		for child in elem.children:
			if isinstance(child, NavigableString):
				# quote=False: this text is going into HTML content, not an
				# attribute value, so quotes/apostrophes don't need escaping
				# (and doing so anyway would show up as literal &#x27; etc.)
				parts.append(html.escape(str(child), quote=False))
			elif child.name in ("script", "style"):
				continue
			elif child.name in INLINE_TAG_MAP:
				wrap = INLINE_TAG_MAP[child.name]
				parts.append(f"<{wrap}>{''.join(_inline_parts(child))}</{wrap}>")
			elif child.name in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br"):
				# block-level children (e.g. two <p>s inside one <blockquote>)
				# are visually separated even with zero literal whitespace
				# between them in the source — force a boundary space so they
				# don't run together; the final collapse-whitespace pass
				# normalizes this against any whitespace that's already there
				parts.append("".join(_inline_parts(child)) + " ")
			else:
				# unknown/structural inline wrapper (a, span, sup, ...) — keep
				# its text content, drop the tag itself
				parts.append("".join(_inline_parts(child)))
		return parts

	def extract_inline(elem):
		return re.sub(r"\s+", " ", "".join(_inline_parts(elem))).strip()

	def build_table_html(table_elem):
		rows_html = []
		for tr in table_elem.find_all("tr", recursive=True):
			cells = tr.find_all(["td", "th"], recursive=False)
			cells_html = "".join(
				f"<{cell.name}>{extract_inline(cell)}</{cell.name}>" for cell in cells
			)
			rows_html.append(f"<tr>{cells_html}</tr>")
		return "<table>" + "".join(rows_html) + "</table>"

	def is_avatar(img_tag, src, alt):
		src_l = src.lower()
		if any(k in src_l for k in [
			"avatar", "profile", "/user/", "/users/", "contributor", "headshot", "author",
			"icon", "logo", "newsletter", "badge", "sponsor",
		]):
			return True
		alt_l = alt.lower()
		if any(k in alt_l for k in [
			"avatar", "profile photo", "author photo", "headshot",
			"icon", "logo", "newsletter",
		]):
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
			if any(k in combined.lower() for k in [
				"author", "avatar", "bio", "contributor", "sidebar", "profile",
				"newsletter", "signup", "subscribe",
			]):
				return True
		return False

	def add_paragraph(text, tag, min_len=20, list_type=None):
		nonlocal char_count
		if len(text) < min_len or text in seen_texts:
			return
		seen_texts.add(text)
		entry = {"text": text, "tag": tag}
		if list_type:
			entry["list_type"] = list_type
		paragraphs.append(entry)
		char_count += len(text)

	block_tags = ["p", "h2", "h3", "h4", "ul", "ol", "blockquote", "pre", "table", "dl"]
	all_tags = block_tags + ["img"]
	raw_matches = content.find_all(all_tags, recursive=True)
	matched_block_ids = {id(m) for m in raw_matches if m.name in block_tags}

	def is_nested_in_block(elem):
		for parent in elem.parents:
			if parent.name in ("html", "body"):
				break
			if id(parent) in matched_block_ids:
				return True
		return False

	# de-dupe nested matches (e.g. a <p> inside a <blockquote> would otherwise
	# be picked up twice), but images are always kept even if nested inside
	# a matched block, since they're independent content
	elements = [m for m in raw_matches if m.name == "img" or not is_nested_in_block(m)]

	for elem in elements:
		if len(paragraphs) >= MAX_PARAGRAPHS or char_count >= MAX_CHARS:
			break

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
				if src not in seen_srcs and not is_avatar(elem, src, alt):
					seen_srcs.add(src)
					images.append({"src": src, "alt": alt, "after_paragraph": len(paragraphs)})

		elif elem.name in ("p", "h2", "h3", "h4"):
			add_paragraph(extract_inline(elem), elem.name)

		elif elem.name == "blockquote":
			add_paragraph(extract_inline(elem), "blockquote", min_len=5)

		elif elem.name in ("ul", "ol"):
			lis = elem.find_all("li", recursive=False)
			if not lis:
				continue
			# skip nav/chrome lists: "related articles" widgets (li's that
			# are just an <a> wrapping the whole li's text, pointing
			# elsewhere) and tag-pill/share-button/byline rows (mostly very
			# short items — real content bullets are mostly substantial)
			def is_pure_link(li):
				links = li.find_all("a")
				return bool(links) and li.get_text(strip=True) == "".join(a.get_text(strip=True) for a in links)
			if sum(is_pure_link(li) for li in lis) / len(lis) >= 0.7:
				continue
			if sum(len(li.get_text(strip=True)) >= 15 for li in lis) / len(lis) < 0.5:
				continue
			for li in lis:
				add_paragraph(extract_inline(li), "li", min_len=15, list_type=elem.name)

		elif elem.name == "dl":
			# flatten term/description pairs into plain paragraphs — dl shows
			# up rarely (e.g. design-credit blocks) and doesn't need its own
			# rendering path
			term = None
			for child in elem.find_all(["dt", "dd"], recursive=False):
				text = extract_inline(child)
				if child.name == "dt":
					term = text
				elif child.name == "dd":
					combined = f"{term}：{text}" if term else text
					add_paragraph(combined, "p", min_len=2)
					term = None

		elif elem.name == "pre":
			# preserve exact whitespace/newlines (don't collapse like prose);
			# escape since paragraph text is embedded unescaped downstream
			text = html.escape(elem.get_text(), quote=False).strip("\n")
			add_paragraph(text, "pre", min_len=1)

		elif elem.name == "table":
			add_paragraph(build_table_html(elem), "table", min_len=1)

	return paragraphs, images


def call_gemini(source_name, title, url, paragraphs):
	from google import genai
	client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

	article_text = "\n\n".join(
		f"[{i}] ({p['tag']}) {p['text']}" for i, p in enumerate(paragraphs)
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
  ],
  "quiz": [
    {{
      "sentence": "一句全新造的英文例句，不可取自文章原文，句中把要考的單字/片語挖空，用 ____ 表示",
      "sentence_zh": "該英文例句的繁體中文翻譯（挖空處請填入正確答案後再整句翻譯，不要翻成「____」）",
      "answer": "被挖空的單字或片語，必須與 vocab 陣列中某個 word 完全一致",
      "options": ["4 個選項字串，其中一個等於 answer，其餘 3 個是從 vocab 陣列中挑的其他單字/片語，順序打散"]
    }}
  ]
}}

規則：
- paragraphs 陣列長度必須與輸入段落數一致
- 每段前面括號標示的是該段的結構類型（p/h2/h3/h4/li/blockquote/pre/table）。除了 pre（程式碼區塊）和 table（表格，內容已是 HTML）以外，每種類型都要正常翻譯成繁體中文。pre 和 table 的 translation 請設為空字串 ""，不要翻譯程式碼或表格內容
- tag 必須是以下其中一個，不可自創：{tag_list}
- vocab 陣列請挑選共 20-24 個單字，比例約為：
  - type "highfreq"（常見但實用的詞，如動詞/形容詞/副詞）約 50%
  - type "general"（值得學習、但不算高頻也不算專業術語的單字）約 40%
  - type "term"（AI/科技/專業術語）約 10%
- 另外額外挑 2-4 個實用片語或慣用語，type 設為 "phrase"，pos 留空字串 ""
- 除了 type "phrase" 以外，每個單字都要標注 pos（詞性縮寫）
- ipa 使用標準 IPA
- quiz 陣列請從 vocab 陣列中挑 10 個不同的單字/片語出題（各類型都可能考），每題的 4 個選項不可重複、且都要來自 vocab 陣列
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
		"quiz": gemini_data.get("quiz", []),
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

	title = html.unescape(selected_entry.get("title", "Untitled"))
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
