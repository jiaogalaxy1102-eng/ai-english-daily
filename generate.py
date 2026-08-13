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
	build_entry,
	entry_filename,
	first_sentence,
	has_signpost,
	render_article,
	render_index,
	render_vocab_page,
	sort_entries,
)

# 2026-08-13：原本是 "gemini-3-flash-preview"，該端點開始回 403 Forbidden
# （preview 版被下架），當天的排程整個失敗。改成 GA 名稱。
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash")

BASE_DIR = Path(__file__).parent
ARTICLES_DIR = BASE_DIR / "articles"
DATA_DIR = BASE_DIR / "data"
SOURCES_FILE = BASE_DIR / "sources.json"
INDEX_FILE = BASE_DIR / "index.html"
VOCAB_FILE = BASE_DIR / "vocab.html"

MAX_PARAGRAPHS = 80
MAX_CHARS = 45000
# 一天只發一篇，寧可換一篇也不要拿太短的東西充數。Simon Willison 這類個人
# 部落格會混進「一段話 + 一個連結」的短貼文，那種東西撐不起一次閱讀。
MIN_PARAGRAPHS = 6
MIN_CHARS = 1200

USER_AGENT = (
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def load_sources():
	with open(SOURCES_FILE, encoding="utf-8") as f:
		return json.load(f)


def existing_dates():
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


# 頁面雜訊：訂閱提示、留言按鈕、分享列、記者聯絡方式、活動廣告。這些只在文章
# 的開頭或結尾出現，所以只從兩端修剪 —— 中段一律不碰，否則正文裡剛好提到
# "subscribe" 的句子會被誤殺。
NOISE_PATTERNS = [
	r"\bsign up\b", r"\bsubscribe\b", r"\bnewsletter\b", r"\byour inbox\b",
	r"\bfollow us\b", r"\bshare this\b", r"\bread later\b", r"\bsave article\b",
	r"^comments?\b", r"\bcomments?$", r"\bthanks for reading\b",
	r"\bsee you (next week|tomorrow)\b", r"^more in:", r"^see all\b",
	r"\bemail digest\b", r"\byou can contact\b", r"\bverify outreach\b",
	r"\bdiscover special offers\b", r"\bupcoming events\b",
	r"\ball rights reserved\b", r"\bcopyright\b",
	r"[\w.+-]+@[\w-]+\.(com|org|net|co)\b",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.I)

# 最多從每一端修掉幾段。設上限是為了讓「規則寫錯」的後果有天花板 —— 就算
# 某個來源的正文開頭剛好連續命中，也不可能把整篇吃掉。
MAX_TRIM_PER_END = 5


def strip_tags(s):
	return re.sub(r"<[^>]+>", "", s)


def is_noise(paragraph):
	text = strip_tags(paragraph["text"])
	if paragraph["tag"] in ("pre", "table"):
		return False
	return bool(NOISE_RE.search(text))


def trim_chrome(paragraphs):
	"""從頭尾兩端修掉導覽/訂閱/留言之類的非內文段落。

	回傳 (保留的段落, 從開頭修掉幾段)。開頭修掉的數量必須回報，因為圖片的
	after_paragraph 是段落索引，前面少了幾段就要跟著平移幾格。
	"""
	start, end = 0, len(paragraphs)
	while start < end and start < MAX_TRIM_PER_END and is_noise(paragraphs[start]):
		start += 1
	trimmed = 0
	while end > start and trimmed < MAX_TRIM_PER_END and is_noise(paragraphs[end - 1]):
		end -= 1
		trimmed += 1
	return paragraphs[start:end], start


def normalize_for_compare(text):
	"""比對用的正規化：去標籤、轉小寫、只留字母數字與空白。

	彎引號與直引號、標點差異都會讓字面比對失效，所以全部抹平再比。
	"""
	plain = re.sub(r"<[^>]+>", " ", text)
	plain = html.unescape(plain).lower()
	plain = re.sub(r"[^a-z0-9\s]", " ", plain)
	return re.sub(r"\s+", " ", plain).strip()


# 比對用的前綴長度。pull quote 通常是內文句子的前半段，比對開頭就夠判斷，
# 整段比反而會因為 pull quote 後面接了署名（"— 某某某, senior fellow"）而失敗。
PULL_QUOTE_PREFIX = 60
PULL_QUOTE_NEIGHBOR_RANGE = 3


def drop_pull_quotes(paragraphs, images):
	"""移除「引述放大框」——內容抄自鄰近內文的 blockquote。

	這種東西在版面上是設計元素，抓下來卻變成一段幾乎一樣的文字：讀者在全文
	階段會連看同一句話兩次，凸點清單也會連續出現兩條重複的骨架。
	"""
	drop = set()
	for i, p in enumerate(paragraphs):
		if p["tag"] != "blockquote":
			continue
		quote = normalize_for_compare(p["text"])
		if len(quote) < 20:
			continue
		prefix = quote[:PULL_QUOTE_PREFIX]
		lo = max(0, i - PULL_QUOTE_NEIGHBOR_RANGE)
		hi = min(len(paragraphs), i + PULL_QUOTE_NEIGHBOR_RANGE + 1)
		for j in range(lo, hi):
			if j == i or paragraphs[j]["tag"] == "blockquote":
				continue
			if prefix and prefix in normalize_for_compare(paragraphs[j]["text"]):
				drop.add(i)
				break

	if not drop:
		return paragraphs, images

	print(f"  移除 {len(drop)} 段引述放大框（內容與鄰近段落重複）")
	kept = [p for i, p in enumerate(paragraphs) if i not in drop]
	# after_paragraph 是「到這裡為止收了幾段」，前面每少一段就要往前移一格
	new_images = []
	for img in images:
		pos = img["after_paragraph"]
		pos -= sum(1 for i in drop if i < pos)
		new_images.append({**img, "after_paragraph": pos})
	return kept, new_images


def extract_content(soup, base_url):
	"""把一份已經 parse 好的 soup 變成 (paragraphs, images)。

	同一套邏輯要能吃兩種輸入：實際爬到的文章頁，以及 RSS 裡附的全文 HTML。
	所以這裡不做任何網路存取，呼叫端負責準備 soup。
	"""
	for tag in soup(["nav", "header", "footer", "aside", "script", "style", "form"]):
		tag.decompose()

	for tag in soup.select('[data-native-ad-id], [class*="native-ad"], [class*="sponsor"], [class*="advertisement"]'):
		tag.decompose()

	# 行內浮層與 UI 裝飾。這些藏在段落「中間」，不是頭尾雜訊，trim_chrome 抓
	# 不到 —— 它們會被壓平成文字混進句子裡。實例：Rest of World 的名詞解釋
	# widget 把「The Alibaba-backed developer」變成「The Alibabai Alibaba,
	# founded in 1999 by ... Tmall.READ MORE-backed developer」。
	for tag in soup.select(
		'[class*="explainer"] [class*="description"], [class*="tooltip"], [class*="popover"], '
		'[class*="info-icon"], [class*="read-more"], '
		'.screen-reader-text, .sr-only, .visually-hidden'
	):
		tag.decompose()

	content = (
		soup.find("article")
		or soup.find("main")
		or soup.find(class_=re.compile(r"post|article|content|entry", re.I))
		or soup.find("body")
		or soup
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

	def int_attr(tag, name):
		"""讀 img 的 width/height 屬性。值可能是 "600px" 或空字串，取不到就給 None。"""
		raw = (tag.get(name) or "").strip().rstrip("px")
		return int(raw) if raw.isdigit() and int(raw) > 0 else None

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
					parsed = urlparse(base_url)
					src = f"{parsed.scheme}://{parsed.netloc}{src}"
				if src not in seen_srcs and not is_avatar(elem, src, alt):
					seen_srcs.add(src)
					img = {"src": src, "alt": alt, "after_paragraph": len(paragraphs)}
					# 原站有寫尺寸就留著，渲染時用 aspect-ratio 先把位置佔住，
					# 圖載入時版面才不會往下跳（CLS）。沒有就照舊不寫。
					w, h = int_attr(elem, "width"), int_attr(elem, "height")
					if w and h:
						img["width"], img["height"] = w, h
					images.append(img)

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


def rss_fulltext_html(entry):
	"""RSS 裡附的全文 HTML。可能在 content（多數 Atom）也可能在 summary
	（Fast Company 那種把整篇塞進 description 的 RSS），取比較長的那個。"""
	candidates = []
	if entry.get("content"):
		candidates.append(entry.content[0].get("value", ""))
	if entry.get("summary"):
		candidates.append(entry.summary)
	return max(candidates, key=len) if candidates else ""


def total_chars(paragraphs):
	return sum(len(p["text"]) for p in paragraphs)


def fetch_article_content(url, rss_html=""):
	"""抓文章內文。頁面與 RSS 全文兩邊都試，取內容比較完整的那一份。

	為什麼不是「RSS 優先」：實測 Ars Technica / 404 Media / Rest of World /
	Simon Willison 的 RSS 都只有節錄，爬蟲反而抓得更多。但 Fast Company 相反
	—— 頁面只吐得出前 34%，全文在 RSS 裡。哪一邊比較完整沒有定則，所以兩邊
	都抓、比字數。這同時讓「頁面被截斷」這種 bug 自動被 RSS 補起來。
	"""
	page_paras, page_imgs = [], []
	try:
		resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
		resp.raise_for_status()
		page_paras, page_imgs = extract_content(BeautifulSoup(resp.text, "lxml"), url)
	except Exception as e:
		print(f"  頁面抓取失敗（{e}），改看 RSS 全文")

	rss_paras, rss_imgs = [], []
	if rss_html:
		try:
			rss_paras, rss_imgs = extract_content(BeautifulSoup(rss_html, "lxml"), url)
		except Exception as e:
			print(f"  RSS 全文解析失敗：{e}")

	page_n, rss_n = total_chars(page_paras), total_chars(rss_paras)
	if rss_n > page_n:
		print(f"  採用 RSS 全文（{rss_n} 字）而非頁面（{page_n} 字）")
		paragraphs, images = rss_paras, rss_imgs
	else:
		paragraphs, images = page_paras, page_imgs

	before = len(paragraphs)
	paragraphs, head_removed = trim_chrome(paragraphs)
	removed = before - len(paragraphs)
	if removed:
		print(f"  頭尾修掉 {removed} 段雜訊（開頭 {head_removed}）")
		images = reindex_images(images, head_removed, len(paragraphs))

	paragraphs, images = drop_pull_quotes(paragraphs, images)

	return paragraphs, images


def reindex_images(images, head_removed, kept_count):
	"""段落被修剪後修正圖片位置，並丟掉落在保留範圍外的圖。"""
	out = []
	for img in images:
		pos = img["after_paragraph"] - head_removed
		if 0 <= pos <= kept_count:
			out.append({**img, "after_paragraph": pos})
	return out


def call_gemini(source_name, title, url, paragraphs):
	from google import genai
	client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

	article_text = "\n\n".join(
		f"[{i}] ({p['tag']}) {p['text']}" for i, p in enumerate(paragraphs)
	)

	tag_list = "、".join(FIXED_TAGS)
	last_index = len(paragraphs) - 1

	# 句首有邏輯路標的段落編號，當成提示給 Gemini。路標是文章作者自己寫的導航詞，
	# 在「哪裡是轉折」這件事上比 AI 事後推論可靠，但只當提示不當答案 —— 有些文章
	# 一個路標都沒有，有些整篇都是。
	signpost_paras = [
		i for i, p in enumerate(paragraphs)
		if p["tag"] not in ("pre", "table", "blockquote") and has_signpost(first_sentence(p["text"]))
	]
	signpost_hint = ", ".join(str(i) for i in signpost_paras) if signpost_paras else "（這篇沒有）"

	prompt = f"""你是一個英文學習助手。以下是一篇來自 {source_name} 的文章：
標題：{title}
網址：{url}

文章內容（每段前有段落編號）：
{article_text}

請完成以下任務，輸出 JSON（不要加 markdown code block）：

{{
  "tag": "從清單中選一個最符合這篇文章主題的分類",
  "summary_en": "3-4 句英文導讀，用比原文簡單的字彙，讓讀者在讀全文前先建立預期",
  "summary_zh": "整篇文章的繁體中文摘要（3-4 句）",
  "conclusion_index": 0,
  "flow": [
    {{
      "paragraph_index": 0,
      "role": "context",
      "text": "從該段落原文照抄的一個完整句子，一字不改"
    }}
  ],
  "noise_indices": [],
  "scan_questions": [
    {{
      "question": "一個英文問題，答案必須能在文章中找到具體事實",
      "answer_zh": "該問題的繁體中文答案",
      "paragraph_index": 0
    }}
  ],
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
      "definition_en": "英英釋義：用比這個字更簡單的英文解釋它，一句話、20字以內",
      "synonyms": ["近義字或近義片語，2-3 個"],
      "example": "自己造的簡短英文例句，含這個字，15字以內，不可取自文章"
    }}
  ]
}}

規則：
- paragraphs 陣列長度必須與輸入段落數一致
- 每段前面括號標示的是該段的結構類型（p/h2/h3/h4/li/blockquote/pre/table）。除了 pre（程式碼區塊）和 table（表格，內容已是 HTML）以外，每種類型都要正常翻譯成繁體中文。pre 和 table 的 translation 請設為空字串 ""，不要翻譯程式碼或表格內容
- tag 必須是以下其中一個，不可自創：{tag_list}
- summary_en 是給「還沒讀全文的人」看的導讀，不要暴雷結論，用字要比原文淺
- conclusion_index 是「整篇結論所在段落」的編號，範圍 0 到 {last_index}。通常在文章後段，但如果這篇沒有明顯結論段，就填 {last_index}
- flow 是這篇文章的「論證走向」，請挑 3-6 句，照文章順序排列，串起來要能讓還沒讀全文的人看出整篇是怎麼推進的（背景 → 主張 → 轉折 → 證據 → 結論）。這不是「挑最重要的幾段」，是「挑出能構成一條邏輯線的幾句」
- flow 的 text 必須是原文中**逐字存在**的完整句子，一個字都不能改（會用字串比對驗證，對不上就整句丟掉）。不要自己造句、不要合併兩句、不要摘要、不要只取半句
- flow 的 role 只能是這五個之一：context（背景/前提）、claim（作者的主張）、turn（轉折/反例/質疑）、evidence（數據或事實佐證）、conclusion（結論）。整條線至少要有一個 claim 和一個 conclusion
- flow 不要挑 blockquote（那是受訪者說的話，不是作者的論證）、pre 或 table 段落
- 以下段落的句首有作者自己寫的邏輯路標（However / Instead / As a result 之類），通常正好是論證轉折處，優先考慮但不必全選：{signpost_hint}
- noise_indices 填「不屬於文章正文」的段落編號：作者自我推銷、訂閱或追蹤呼籲、贊助商訊息、編按、相關文章連結列表。這些段落會整段不顯示，所以判斷不確定就不要填，寧可漏掉也不要誤刪正文。沒有就給空陣列 []
- 被列入 noise_indices 的段落不會顯示給讀者，所以 flow 與 scan_questions 都不可以取自這些段落
- scan_questions 請出 3 題。問題用英文、答案用繁體中文，paragraph_index 標明答案出現在第幾段。問題要問具體事實（誰、多少、什麼技術、造成什麼結果），不要問感想或推論
- vocab 陣列請挑選共 20-24 個單字，比例約為：
  - type "highfreq"（常見但實用的詞，如動詞/形容詞/副詞）約 50%
  - type "general"（值得學習、但不算高頻也不算專業術語的單字）約 40%
  - type "term"（AI/科技/專業術語）約 10%
- 另外額外挑 2-4 個實用片語或慣用語，type 設為 "phrase"，pos 留空字串 ""
- 除了 type "phrase" 以外，每個單字都要標注 pos（詞性縮寫）
- ipa 使用標準 IPA
- definition_en 是給中級英文學習者看的英英釋義，只能用比該單字更簡單常見的英文；不可以在釋義裡再用該單字本身或它的變化形
- synonyms 給 2-3 個真的能替換的近義字（片語類型就給近義片語）。想不到夠貼切的就給空陣列 []，不要硬湊
- example 是你自己造的短句，句子要簡單、日常、能一眼看懂該單字怎麼用，不可以從文章裡抄句子
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


def clamp_index(value, hi, default):
	try:
		i = int(value)
	except (TypeError, ValueError):
		return default
	return i if 0 <= i <= hi else default


FLOW_ROLES = ("context", "claim", "turn", "evidence", "conclusion")
FLOW_MIN = 3
FLOW_MAX = 6


def validate_flow(flow, paragraphs):
	"""確認 flow 的每一句都真的逐字出現在文章裡。

	凸點階段直接把這些句子當「文章的骨架」呈現，讀者會以為那就是原文。Gemini
	很容易把兩句縫成一句、或順手改個連接詞 —— 讀起來很順，但那句話文章裡並不
	存在，後面讀全文時對不上。所以這裡用 `normalize_for_compare`（抹平大小寫與
	標點）做子字串比對，對不上就丟掉那一句。

	剩不到 FLOW_MIN 句就回傳空陣列，讓 `template.py` 退到第二層（路標篩選）——
	殘缺的邏輯線比沒有更誤導。
	"""
	if not isinstance(flow, list):
		return []

	bodies = [normalize_for_compare(p["text"]) for p in paragraphs]
	last = len(paragraphs) - 1
	cleaned = []

	for item in flow:
		if not isinstance(item, dict):
			continue
		text = (item.get("text") or "").strip()
		needle = normalize_for_compare(text)
		if not needle:
			continue

		idx = clamp_index(item.get("paragraph_index"), last, -1)
		# 先比 AI 指定的那一段，對不上再全篇找 —— 句子是真的、只是段號記錯，
		# 這種情況救得回來，沒必要連句子一起丟。
		if idx >= 0 and needle in bodies[idx]:
			found = idx
		else:
			found = next((i for i, b in enumerate(bodies) if needle in b), -1)

		if found < 0:
			print(f"  骨架有一句在原文找不到，捨棄：{text[:60]}...")
			continue

		role = item.get("role")
		if role not in FLOW_ROLES:
			role = "conclusion" if found == last else "claim"

		cleaned.append({"paragraph_index": found, "role": role, "text": text})

	# 去重（同一段被挑兩次）並照文章順序排，AI 偶爾會亂序輸出
	seen = set()
	ordered = []
	for item in sorted(cleaned, key=lambda x: x["paragraph_index"]):
		key = normalize_for_compare(item["text"])
		if key in seen:
			continue
		seen.add(key)
		ordered.append(item)

	if len(ordered) < FLOW_MIN:
		print(f"  骨架只剩 {len(ordered)} 句，不足 {FLOW_MIN} 句，改用路標篩選")
		return []
	return ordered[:FLOW_MAX]


# 一篇文章最多容許多少比例被判成雜訊。頭尾的推銷段落通常一兩段，超過兩成
# 幾乎可以確定是 AI 誤判，寧可整批不採用。
NOISE_MAX_RATIO = 0.2


def validate_noise_indices(raw, paragraphs):
	"""把 AI 標的雜訊段落編號濾成合法範圍內的整數。

	這些段落會整段不顯示，所以設了 `NOISE_MAX_RATIO` 上限：AI 一旦誤判、
	一口氣標掉半篇文章，寧可整批不採用，也不要讓讀者看到一篇缺角的文章。
	"""
	if not isinstance(raw, list):
		return []
	last = len(paragraphs) - 1
	picked = sorted({i for i in raw if isinstance(i, int) and not isinstance(i, bool) and 0 <= i <= last})
	if len(picked) > max(1, int(len(paragraphs) * NOISE_MAX_RATIO)):
		print(f"  AI 標了 {len(picked)}/{len(paragraphs)} 段是雜訊，比例過高，整批不採用")
		return []
	return picked


def build_article_data(source_name, title, url, date_str, tag, paragraphs, images, gemini_data):
	translations = {p["index"]: p["translation"] for p in gemini_data["paragraphs"]}
	last = len(paragraphs) - 1

	questions = []
	for q in gemini_data.get("scan_questions", []):
		questions.append({
			"question": q.get("question", ""),
			"answer_zh": q.get("answer_zh", ""),
			"paragraph_index": clamp_index(q.get("paragraph_index"), last, 0),
		})

	return {
		"date": date_str,
		"tag": tag,
		"title": title,
		"source_name": source_name,
		"url": url,
		"summary_en": gemini_data.get("summary_en", ""),
		"summary_zh": gemini_data.get("summary_zh", ""),
		"conclusion_index": clamp_index(gemini_data.get("conclusion_index"), last, last),
		"flow": validate_flow(gemini_data.get("flow", []), paragraphs),
		"noise_indices": validate_noise_indices(gemini_data.get("noise_indices", []), paragraphs),
		"scan_questions": questions,
		"paragraphs": [
			{"tag": p["tag"], "text": p["text"], "translation": translations.get(i, "")}
			for i, p in enumerate(paragraphs)
		],
		"images": images,
		"vocab": gemini_data["vocab"],
	}


def gather_candidates(sources, used):
	"""把所有來源的未使用文章收成一張候選清單。

	舊版是「隨機挑一個來源、拿它的第一篇」，來源掛掉就默默 continue ——
	VentureBeat 的 RSS 死掉半個月都沒人發現就是這樣來的。現在改成先全部收集，
	並且記錄有幾個來源失敗，讓 main() 能在全掛的時候讓 workflow 紅燈。
	"""
	candidates = []
	failures = []
	for source in sources:
		try:
			entries = fetch_feed(source["rss"])
		except Exception as e:
			failures.append(f"{source['name']}: {e}")
			continue
		if not entries:
			failures.append(f"{source['name']}: RSS 沒有任何文章")
			continue
		for entry in entries:
			link = entry.get("link", "")
			if link and link not in used:
				candidates.append((source, entry))
	return candidates, failures


def main():
	DATA_DIR.mkdir(exist_ok=True)
	ARTICLES_DIR.mkdir(exist_ok=True)
	sources = load_sources()
	done = existing_dates()
	today = os.environ.get("DATE_OVERRIDE") or datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

	if today in done:
		print(f"Article for {today} already exists, skipping.")
		sys.exit(0)

	used = used_urls()
	random.shuffle(sources)
	candidates, failures = gather_candidates(sources, used)

	for msg in failures:
		print(f"來源失敗 — {msg}")

	if failures and len(failures) == len(sources):
		print("所有來源都抓不到，這不是正常狀況，讓 workflow 失敗以便追查。")
		sys.exit(1)

	if not candidates:
		print("所有來源都沒有未使用的新文章，今天跳過。")
		sys.exit(0)

	# 同一個來源的文章會連在一起，打散避免連續好幾天都同一家
	random.shuffle(candidates)

	selected = None
	for source, entry in candidates[:12]:
		title = html.unescape(entry.get("title", "Untitled"))
		url = entry.get("link", "")
		print(f"嘗試：{title} （{source['name']}）")
		try:
			paragraphs, images = fetch_article_content(url, rss_fulltext_html(entry))
		except Exception as e:
			print(f"  抓取失敗：{e}")
			continue
		if len(paragraphs) < MIN_PARAGRAPHS or total_chars(paragraphs) < MIN_CHARS:
			print(f"  太短（{len(paragraphs)} 段 / {total_chars(paragraphs)} 字），換下一篇")
			continue
		selected = (source["name"], title, url, paragraphs, images)
		break

	if not selected:
		print("候選文章都太短或抓不到內容，今天跳過。")
		sys.exit(0)

	source_name, title, url, paragraphs, images = selected
	print(f"採用：{title}（{source_name}，{len(paragraphs)} 段 / {total_chars(paragraphs)} 字）")

	try:
		gemini_data = call_gemini(source_name, title, url, paragraphs)
	except Exception as e:
		print(f"Gemini error: {e}")
		sys.exit(1)

	tag = gemini_data.get("tag")
	if tag not in FIXED_TAGS:
		print(f"Unexpected tag '{tag}' from Gemini, falling back to '產品與應用'.")
		tag = "產品與應用"

	data = build_article_data(source_name, title, url, today, tag, paragraphs, images, gemini_data)

	data_path = DATA_DIR / f"{today}.json"
	data_path.write_text(json.dumps(data, ensure_ascii=False, indent="\t"), encoding="utf-8")
	print(f"Written: {data_path}")

	entries = load_all_entries()

	out_path = ARTICLES_DIR / entry_filename(data)
	out_path.write_text(render_article(data, entries), encoding="utf-8")
	print(f"Written: {out_path}")

	INDEX_FILE.write_text(render_index(entries), encoding="utf-8")
	VOCAB_FILE.write_text(render_vocab_page(), encoding="utf-8")
	print("Done.")


if __name__ == "__main__":
	main()
