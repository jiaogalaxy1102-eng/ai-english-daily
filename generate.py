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

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-3-flash-preview"

BASE_DIR = Path(__file__).parent
ARTICLES_DIR = BASE_DIR / "articles"
SOURCES_FILE = BASE_DIR / "sources.json"
INDEX_FILE = BASE_DIR / "index.html"

MAX_PARAGRAPHS = 12
MAX_CHARS = 6000


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


def build_article_html(source_name, title, url, date_str, paragraphs, images, gemini_data):
	translations = {p["index"]: p["translation"] for p in gemini_data["paragraphs"]}
	vocab = gemini_data["vocab"]

	# build lookup: word -> vocab entry
	vocab_map = {v["word"].lower(): v for v in vocab}
	highfreq_words = {v["word"].lower() for v in vocab if v["type"] == "highfreq"}
	term_words = {v["word"].lower() for v in vocab if v["type"] == "term"}

	# escape JSON for embedding in HTML
	vocab_json = json.dumps(vocab, ensure_ascii=False)

	# highlight words in a paragraph text
	def highlight(text):
		# sort by length descending to match longer phrases first
		all_words = sorted(vocab_map.keys(), key=len, reverse=True)
		for word in all_words:
			entry = vocab_map[word]
			css_class = "word-highfreq" if entry["type"] == "highfreq" else "word-term"
			pattern = re.compile(re.escape(word), re.IGNORECASE)
			escaped_word = lambda m: (
				f'<span class="{css_class}" data-word="{entry["word"]}">'
				f'{m.group()}</span>'
			)
			text = pattern.sub(escaped_word, text, count=1)
		return text

	# group images by after_paragraph index
	images_by_para = {}
	for img in images:
		idx = img["after_paragraph"]
		images_by_para.setdefault(idx, []).append(img)

	# build paragraphs HTML
	paras_html = ""
	for i, para in enumerate(paragraphs):
		tag = para["tag"]
		original_highlighted = highlight(para["text"])

		if tag in ["h2", "h3"]:
			paras_html += f"""
		<div class="para-block heading-block">
			<{tag} class="para-original">{original_highlighted}</{tag}>
			<{tag} class="para-translation">{translations.get(i, "")}</{tag}>
		</div>"""
		else:
			paras_html += f"""
		<div class="para-block">
			<p class="para-original">{original_highlighted}</p>
			<p class="para-translation">{translations.get(i, "")}</p>
		</div>"""

		# insert images after this paragraph
		for img in images_by_para.get(i, []):
			paras_html += f"""
		<figure class="article-image">
			<img src="{img['src']}" alt="{img['alt']}" loading="lazy">
			{f'<figcaption>{img["alt"]}</figcaption>' if img['alt'] else ''}
		</figure>"""

	# build vocab summary
	vocab_summary = ""
	for v in vocab:
		badge_class = "badge-highfreq" if v["type"] == "highfreq" else "badge-term"
		badge_label = "高頻詞" if v["type"] == "highfreq" else "術語"
		vocab_summary += f"""
			<div class="vocab-card" onclick="showPopup('{v['word']}')">
				<span class="vocab-word">{v['word']}</span>
				<span class="badge {badge_class}">{badge_label}</span>
				<span class="vocab-ipa">{v['ipa']}</span>
				<span class="vocab-def">{v['definition_zh']}</span>
			</div>"""

	return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title>{title} — AI English Daily</title>
	<link rel="stylesheet" href="../style.css">
</head>
<body>

<header class="site-header site-header--nav">
	<a href="../index.html">AI English Daily</a>
	<span style="color:#6b5f50">/ {date_str}</span>
</header>

<div class="article-header">
	<div class="article-source">{source_name}</div>
	<h1 class="article-title">{title}</h1>
	<div class="article-meta">
		{date_str} &nbsp;·&nbsp; <a href="{url}" target="_blank" rel="noopener">原文連結</a>
	</div>
</div>

<div class="vocab-section">
	<button class="vocab-toggle" onclick="toggleVocab(this)">
		<span>今日詞彙表</span><span class="toggle-arrow">▼</span>
	</button>
	<div class="vocab-grid" id="vocab-grid">
		{vocab_summary}
	</div>
</div>

<div class="article-body">
	{paras_html}
</div>

<div class="popup-overlay" id="popup-overlay" onclick="closePopupOnOverlay(event)">
	<div class="popup-card" id="popup-card">
		<button class="btn-close" onclick="closePopup()">×</button>
		<div class="popup-word" id="popup-word"></div>
		<div class="popup-ipa" id="popup-ipa"></div>
		<div class="popup-badge" id="popup-badge"></div>
		<div class="popup-def" id="popup-def"></div>
		<div class="popup-example" id="popup-example"></div>
		<div class="popup-actions">
			<button class="btn-speak" id="btn-speak" onclick="speakWord()">發音</button>
			<button class="btn-known" id="btn-known" onclick="toggleKnown()">已認識</button>
		</div>
	</div>
</div>

<script>
	const vocabData = {vocab_json};
	const vocabMap = {{}};
	vocabData.forEach(v => {{ vocabMap[v.word.toLowerCase()] = v; }});

	const knownKey = "known-words";
	function getKnown() {{
		return JSON.parse(localStorage.getItem(knownKey) || "{{}}");
	}}

	let currentWord = null;

	function showPopup(word) {{
		const entry = vocabMap[word.toLowerCase()];
		if (!entry) return;
		currentWord = entry.word;

		document.getElementById("popup-word").textContent = entry.word;
		document.getElementById("popup-ipa").textContent = entry.ipa;
		document.getElementById("popup-def").textContent = entry.definition_zh;
		document.getElementById("popup-example").textContent = entry.example || "";

		const badge = document.getElementById("popup-badge");
		badge.className = "popup-badge";
		badge.innerHTML = entry.type === "highfreq"
			? '<span class="badge badge-highfreq">高頻詞</span>'
			: '<span class="badge badge-term">術語</span>';

		const known = getKnown();
		const btn = document.getElementById("btn-known");
		btn.classList.toggle("marked", !!known[entry.word]);

		document.getElementById("popup-overlay").classList.add("open");
	}}

	function closePopup() {{
		document.getElementById("popup-overlay").classList.remove("open");
		currentWord = null;
	}}

	function closePopupOnOverlay(e) {{
		if (e.target === document.getElementById("popup-overlay")) closePopup();
	}}

	function speakWord() {{
		if (!currentWord) return;
		const utter = new SpeechSynthesisUtterance(currentWord);
		utter.lang = "en-US";
		speechSynthesis.speak(utter);
	}}

	function toggleKnown() {{
		if (!currentWord) return;
		const known = getKnown();
		if (known[currentWord]) {{
			delete known[currentWord];
		}} else {{
			known[currentWord] = true;
		}}
		localStorage.setItem(knownKey, JSON.stringify(known));
		document.getElementById("btn-known").classList.toggle("marked", !!known[currentWord]);
	}}

	function toggleVocab(btn) {{
		const grid = document.getElementById("vocab-grid");
		grid.classList.toggle("open");
		btn.querySelector(".toggle-arrow").textContent = grid.classList.contains("open") ? "▲" : "▼";
	}}

	// wire up highlighted words
	document.querySelectorAll(".word-highfreq, .word-term").forEach(el => {{
		el.addEventListener("click", () => showPopup(el.dataset.word));
	}});

	// close on Escape
	document.addEventListener("keydown", e => {{ if (e.key === "Escape") closePopup(); }});
</script>

</body>
</html>"""


def update_index(date_str, title, source_name, filename):
	index_path = INDEX_FILE
	entry_html = f'<li><a href="articles/{filename}">{date_str} — {title} <span class="source-tag">{source_name}</span></a></li>'

	if not index_path.exists():
		return  # initial index.html will be committed separately

	content = index_path.read_text(encoding="utf-8")
	insert_after = '<ul id="article-list">'
	content = content.replace(insert_after, insert_after + "\n\t\t\t" + entry_html, 1)
	index_path.write_text(content, encoding="utf-8")


def main():
	sources = load_sources()
	done = existing_dates()
	today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

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

	html = build_article_html(source_name, title, url, today, paragraphs, images, gemini_data)

	filename = f"{today}.html"
	out_path = ARTICLES_DIR / filename
	out_path.write_text(html, encoding="utf-8")
	print(f"Written: {out_path}")

	update_index(today, title, source_name, filename)
	print("Done.")


if __name__ == "__main__":
	main()
