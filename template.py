import html as htmllib
import json
import random
import re

CSS_VERSION = 13

FIXED_TAGS = [
	"模型與研究",
	"產品與應用",
	"產業與商業",
	"政策與倫理",
	"資安與隱私",
	"觀點評論",
]

VOCAB_TYPE_INFO = {
	"highfreq": ("word-highfreq", "badge-highfreq", "高頻詞"),
	"general": ("word-general", "badge-general", "學習詞"),
	"term": ("word-term", "badge-term", "術語"),
	"phrase": ("word-phrase", "badge-phrase", "片語"),
}

WENKAI_FONT_CSS = "https://cdn.jsdelivr.net/npm/lxgw-wenkai-tc-webfont@1/lxgwwenkaitc-regular.css"

# reads saved settings before first paint so the page never flashes the default theme
SETTINGS_INIT_SCRIPT = """<script>
(function() {
	try {
		var s = JSON.parse(localStorage.getItem("site-settings") || "{}");
		var html = document.documentElement;
		if (s.palette) html.setAttribute("data-palette", s.palette);
		if (s.fontZh) html.setAttribute("data-font-zh", s.fontZh);
		if (s.fontEn) html.setAttribute("data-font-en", s.fontEn);
		if (s.fontScale) html.style.fontSize = s.fontScale + "%";
	} catch (e) {}
})();
</script>"""

SETTINGS_PANEL_HTML = """
<button class="settings-trigger" id="settings-trigger" onclick="toggleSettings()" aria-label="顯示設定">⋯</button>
<div class="settings-overlay" id="settings-overlay" onclick="closeSettingsOnOverlay(event)">
	<div class="settings-panel" id="settings-panel">
		<div class="settings-row">
			<div class="settings-label">配色</div>
			<div class="palette-swatches">
				<button class="swatch-btn" data-palette="coffee" style="background:#f8f6f1" onclick="setPalette('coffee')" aria-label="咖啡歐蕾"></button>
				<button class="swatch-btn" data-palette="paper" style="background:#ffffff" onclick="setPalette('paper')" aria-label="純白經典"></button>
				<button class="swatch-btn" data-palette="almond" style="background:#fbf3e7" onclick="setPalette('almond')" aria-label="溫柔杏米"></button>
				<button class="swatch-btn" data-palette="sage" style="background:#e8f0e3" onclick="setPalette('sage')" aria-label="護眼豆沙綠"></button>
				<button class="swatch-btn" data-palette="night" style="background:#1b1a17" onclick="setPalette('night')" aria-label="深夜暗色"></button>
				<button class="swatch-btn" data-palette="slate" style="background:#eef1f4" onclick="setPalette('slate')" aria-label="手帳藍灰"></button>
			</div>
		</div>
		<div class="settings-row">
			<div class="settings-label">中文字體</div>
			<select id="font-zh-select" onchange="setFontZh(this.value)">
				<option value="default">系統預設</option>
				<option value="wenkai">霞鶩文楷 TC</option>
			</select>
		</div>
		<div class="settings-row">
			<div class="settings-label">英文字體</div>
			<select id="font-en-select" onchange="setFontEn(this.value)">
				<option value="default">系統預設</option>
				<option value="georgia">Georgia（襯線體）</option>
				<option value="nanum">Nanum Pen Script（手寫體）</option>
			</select>
		</div>
		<div class="settings-row">
			<div class="settings-label">字級</div>
			<div class="font-size-control">
				<button type="button" onclick="adjustFontSize(-1)" aria-label="縮小字級">A-</button>
				<span id="font-size-display">100%</span>
				<button type="button" onclick="adjustFontSize(1)" aria-label="放大字級">A+</button>
			</div>
		</div>
	</div>
</div>"""

# 前端存單字用的 localStorage key。文章頁（加入）跟 vocab.html（匯出）共用，
# 改名字要兩邊一起改，否則已收集的單字會突然消失。
ANKI_STORAGE_KEY = "anki-deck"


def entry_filename(d):
	return f"{d['date']}.html"


def build_entry(d):
	return {
		"date": d["date"],
		"title": d["title"],
		"source_name": d["source_name"],
		"filename": entry_filename(d),
		"tag": d.get("tag", ""),
	}


def sort_entries(entries):
	"""Newest first."""
	return sorted(entries, key=lambda e: e["date"], reverse=True)


def pick_related(current_filename, current_tag, all_entries, n=3):
	others = [e for e in all_entries if e["filename"] != current_filename]
	same_tag = [e for e in others if current_tag and e.get("tag") == current_tag]
	chosen = same_tag[:n]
	if len(chosen) < n:
		pool = [e for e in others if e not in chosen]
		random.shuffle(pool)
		chosen += pool[: n - len(chosen)]
	return chosen


# 句號不等於句子結束。這幾類是實際會誤切的來源：頭銜與縮寫（Mr. / Inc.）、
# 姓名縮寫（J. Smith）、小數（3.5）。清單不可能窮盡，遇到怪切法就往這裡加。
ABBREVIATIONS = {
	"mr", "mrs", "ms", "dr", "prof", "st", "vs", "etc", "inc", "ltd", "co",
	"corp", "jr", "sr", "u.s", "u.k", "e.g", "i.e", "a.m", "p.m", "approx",
	"dept", "est", "fig", "gen", "gov", "sen", "rep", "vol", "no", "al",
	"ph.d", "d.c", "a.i",
}

SENTENCE_BREAK_RE = re.compile(r'(?<=[.!?])["”\'\)\]]*\s+')

# 只用來擋退化情況（"Short." 這種一個詞的句子當凸點沒有意義，寧可併下一句）。
# 縮寫誤切是由下面的 token 檢查擋的，不要用長度來擋 —— 訂太高會把
# "Dr. Chen led the study." 這種合法的短句也濾掉，整段原封不動吐回來。
MIN_SENTENCE_CHARS = 12


def strip_tags(text):
	return re.sub(r"<[^>]+>", "", text)


def first_sentence(text, max_chars=220):
	"""抓一段的第一句，給凸點預覽用。

	不存進 data/*.json —— 這是呈現層的事，演算法改了跑 rebuild.py 就好，
	不用重新生成資料。
	"""
	plain = re.sub(r"\s+", " ", strip_tags(text)).strip()
	plain = htmllib.unescape(plain)
	for m in SENTENCE_BREAK_RE.finditer(plain):
		candidate = plain[: m.start()].strip()
		if len(candidate) < MIN_SENTENCE_CHARS:
			continue
		last = re.search(r"(\S+)$", candidate)
		token = last.group(1).rstrip(".\"”')]").lower() if last else ""
		if token in ABBREVIATIONS:
			continue
		if re.fullmatch(r"[a-z]", token):  # 姓名縮寫 J. Smith
			continue
		if re.fullmatch(r"[\d.,]+", token):  # 小數 3.5
			continue
		nxt = plain[m.end():]
		if nxt and not (nxt[0].isupper() or nxt[0].isdigit() or nxt[0] in '"“'):
			continue
		return candidate
	if len(plain) <= max_chars:
		return plain
	return plain[:max_chars].rsplit(" ", 1)[0] + "…"


# 只有這幾個行內標籤允許出現在 AI 產出的文字裡。爬蟲抓下來的英文原文在
# extract_content() 就已經處理過，這裡管的是 Gemini 回傳的翻譯與詞彙欄位。
ALLOWED_INLINE_TAGS = ("em", "strong", "code")


def safe_inline(text):
	"""把 AI 產出的文字放進 HTML 之前先跳脫，只放行少數行內標籤。

	為什麼需要：文章內文是從第三方網站爬來的，會原封不動進 Gemini 的 prompt。
	一篇惡意文章可以在內文裡夾帶指令，誘導 Gemini 把 <script> 寫進翻譯欄位，
	那段東西會被 GitHub Actions 自動 commit 並發佈到公開網站上，全程沒有人看
	過。跳脫是這條鏈上唯一的關卡。
	"""
	escaped = htmllib.escape(text or "", quote=False)
	for tag in ALLOWED_INLINE_TAGS:
		escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>")
		escaped = escaped.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
	return escaped


# 邏輯路標：告訴讀者這一段跟上一段是什麼關係的詞。略讀時看到它們就能推斷
# 文章的論證怎麼走，不用讀完整段。
#
# 只比對「句首」是刻意的 —— 真正有導航作用的是開頭那個詞。把句中每個 but
# 都標起來只會變成滿頁螢光筆，反而看不出結構。少數幾個逗號後的轉折詞例外
# （", yet" 這種），那些同樣是在轉折。
SIGNPOSTS_SENTENCE_START = [
	"notably absent", "the one surprising note", "whatever the cause",
	"on the other hand", "in contrast", "as a result", "in addition",
	"at the same time", "even so", "that said", "in fact", "indeed",
	"however", "nevertheless", "nonetheless", "still", "yet", "but",
	"although", "though", "despite", "while", "whereas", "instead",
	"therefore", "thus", "consequently", "meanwhile", "moreover",
	"furthermore", "besides", "notably", "importantly", "crucially",
	"surprisingly", "admittedly", "finally", "eventually", "then",
]

SIGNPOSTS_MID = [", yet", ", but", ", however", ", although", ", though", ", while"]

_START_RE = re.compile(
	r'^(["“\']?)(' + "|".join(sorted((re.escape(s) for s in SIGNPOSTS_SENTENCE_START), key=len, reverse=True)) + r')\b',
	re.IGNORECASE,
)
_MID_RE = re.compile(
	"(" + "|".join(sorted((re.escape(s) for s in SIGNPOSTS_MID), key=len, reverse=True)) + r')\b',
	re.IGNORECASE,
)


def mark_signposts(escaped_sentence):
	"""在已經跳脫過的句子裡標出邏輯路標。輸入必須是跳脫後的文字。"""
	def wrap(m):
		lead = m.group(1)
		word = m.group(2)
		return f'{lead}<span class="signpost">{word}</span>'

	out, n = _START_RE.subn(wrap, escaped_sentence, count=1)
	if n:
		return out

	def wrap_mid(m):
		token = m.group(1)
		punct, word = token[0], token[1:].lstrip()
		return f'{punct} <span class="signpost">{word}</span>'

	return _MID_RE.sub(wrap_mid, escaped_sentence, count=1)


def highlight(text, vocab_map):
	# sort by length descending to match longer phrases first
	all_words = sorted(vocab_map.keys(), key=len, reverse=True)
	for word in all_words:
		entry = vocab_map[word]
		css_class = VOCAB_TYPE_INFO.get(entry["type"], VOCAB_TYPE_INFO["highfreq"])[0]
		word_attr = htmllib.escape(entry["word"], quote=True)
		pattern = re.compile(re.escape(word), re.IGNORECASE)
		escaped_word = lambda m, c=css_class, a=word_attr: (
			f'<span class="{c}" data-word="{a}">'
			f'{m.group()}</span>'
		)
		text = pattern.sub(escaped_word, text, count=1)
	return text


def build_bumps_html(data):
	"""凸點預覽：AI 英文導讀 + 每段首句 + 標出結論段。

	目的是讀全文前先看到骨架，所以這一階段全部維持英文 —— 中文摘要留到
	第三階段（驗證）才出現，不然「先看骨架」就變成「先看翻譯」。
	"""
	paragraphs = data["paragraphs"]
	conclusion_index = data.get("conclusion_index", len(paragraphs) - 1)

	rows = ""
	for i, p in enumerate(paragraphs):
		tag = p["tag"]
		# blockquote 排除在骨架之外：那是被引用者說的話，不是作者的論證脈絡。
		# 混進來會把邏輯線打斷（實例：07-31 有 3/11 條骨架其實是信件原文）。
		if tag in ("pre", "table", "li", "blockquote"):
			continue
		if tag in ("h2", "h3", "h4"):
			rows += f'\n\t\t<div class="bump bump-heading">{htmllib.escape(strip_tags(p["text"]))}</div>'
			continue
		sentence = first_sentence(p["text"])
		if not sentence:
			continue
		is_conclusion = i == conclusion_index
		cls = "bump bump-conclusion" if is_conclusion else "bump"
		label = '<span class="bump-label">結論</span>' if is_conclusion else ""
		body = mark_signposts(htmllib.escape(sentence))
		rows += f'\n\t\t<div class="{cls}">{label}{body}</div>'

	summary_en = data.get("summary_en", "")
	summary_html = ""
	if summary_en:
		summary_html = f"""
	<p class="bumps-summary">{htmllib.escape(summary_en)}</p>"""

	return f"""
<section class="stage stage-bumps content-block" id="stage-bumps">
	<div class="stage-hint">先看骨架，建立預期再讀全文</div>{summary_html}
	<div class="bumps-list">{rows}
	</div>
	<div class="stage-actions">
		<button class="btn-primary" onclick="goStage('full')">開始讀全文 →</button>
		<button class="btn-quiet" onclick="goStage('verify')">直接看全文與翻譯</button>
	</div>
</section>"""


def build_scan_questions_html(data):
	questions = data.get("scan_questions") or []
	if not questions:
		return "", ""

	items = ""
	for i, q in enumerate(questions):
		items += f"""
		<li class="scan-question">
			<span class="scan-q-num">{i + 1}</span>
			<span class="scan-q-text">{htmllib.escape(q.get("question", ""))}</span>
		</li>"""
	pinned = f"""
<section class="scan-panel content-block" id="scan-panel">
	<div class="scan-panel-label">邊讀邊找這幾件事</div>
	<ul class="scan-list">{items}
	</ul>
</section>"""

	answers = ""
	for i, q in enumerate(questions):
		answers += f"""
		<li class="scan-answer">
			<span class="scan-q-num">{i + 1}</span>
			<div>
				<div class="scan-a-question">{htmllib.escape(q.get("question", ""))}</div>
				<div class="scan-a-text">{htmllib.escape(q.get("answer_zh", ""))}</div>
			</div>
		</li>"""
	answers_html = f"""
	<div class="scan-answers">
		<div class="section-label">掃讀問題解答</div>
		<ul class="scan-answer-list">{answers}
		</ul>
	</div>"""
	return pinned, answers_html


def render_article(data, all_entries=None):
	"""data: 一篇文章的 dict（見 data/{date}.json）。
	all_entries: 全部已發布文章的 entry 清單，用來產生底部的延伸閱讀。"""
	all_entries = all_entries or []
	vocab = data["vocab"]
	vocab_map = {v["word"].lower(): v for v in vocab}

	# escape JSON for embedding in HTML — prevent </script> from breaking the tag
	vocab_json = json.dumps(vocab, ensure_ascii=False).replace("</script>", r"<\/script>")

	quiz = data.get("quiz") or []
	quiz_json = json.dumps(quiz, ensure_ascii=False).replace("</script>", r"<\/script>")

	# group images by after_paragraph index
	images_by_para = {}
	for img in data["images"]:
		idx = img["after_paragraph"]
		images_by_para.setdefault(idx, []).append(img)

	def image_figure(img):
		src_e = htmllib.escape(img['src'], quote=True)
		alt_e = htmllib.escape(img['alt'], quote=True)
		caption = f'<figcaption>{htmllib.escape(img["alt"])}</figcaption>' if img['alt'] else ''
		return f"""
		<figure class="article-image">
			<img src="{src_e}" alt="{alt_e}" loading="lazy">
			{caption}
		</figure>"""

	reveal_btn = '<button class="para-reveal" onclick="revealPara(this)" aria-label="顯示中文">看中文</button>'

	# build paragraphs HTML
	# after_paragraph == 0 means the image appeared before any paragraph
	# (e.g. a lead image), so it goes before the loop below
	paras_html = "".join(image_figure(img) for img in images_by_para.get(0, []))
	all_paras = data["paragraphs"]
	i = 0
	while i < len(all_paras):
		para = all_paras[i]
		tag = para["tag"]

		# after_paragraph is recorded as "how many paragraphs collected so far"
		# at scrape time, which is i+1 once this (i-th, 0-indexed) paragraph is added

		if tag == "li":
			# group consecutive li's of the same list type into one <ul>/<ol>,
			# pairing each bullet with its own translation underneath
			list_type = para.get("list_type", "ul")
			items_html = ""
			while i < len(all_paras) and all_paras[i]["tag"] == "li" and all_paras[i].get("list_type") == list_type:
				p = all_paras[i]
				items_html += f"""
				<li>{highlight(p["text"], vocab_map)}<span class="li-translation">{safe_inline(p.get("translation", ""))}</span></li>"""
				for img in images_by_para.get(i + 1, []):
					items_html += image_figure(img)
				i += 1
			paras_html += f"""
		<div class="para-block list-block">
			<{list_type} class="para-original">{items_html}
			</{list_type}>
			{reveal_btn}
		</div>"""
			continue

		if tag in ("h2", "h3", "h4"):
			original_highlighted = highlight(para["text"], vocab_map)
			translation = safe_inline(para.get("translation", ""))
			paras_html += f"""
		<div class="para-block heading-block">
			<{tag} class="para-original">{original_highlighted}</{tag}>
			<{tag} class="para-translation">{translation}</{tag}>
		</div>"""
		elif tag == "blockquote":
			original_highlighted = highlight(para["text"], vocab_map)
			translation = safe_inline(para.get("translation", ""))
			paras_html += f"""
		<div class="para-block quote-block">
			<blockquote class="para-original">{original_highlighted}</blockquote>
			<blockquote class="para-translation">{translation}</blockquote>
			{reveal_btn}
		</div>"""
		elif tag == "pre":
			# code isn't translated or vocab-highlighted; text is already
			# HTML-escaped at scrape time (see extract_content)
			paras_html += f"""
		<div class="para-block code-block">
			<pre class="para-original"><code>{para["text"]}</code></pre>
		</div>"""
		elif tag == "table":
			# already-built, already-escaped <table>...</table> HTML from
			# scrape time; not translated or vocab-highlighted
			paras_html += f"""
		<div class="para-block table-block">
			{para["text"]}
		</div>"""
		else:
			original_highlighted = highlight(para["text"], vocab_map)
			translation = safe_inline(para.get("translation", ""))
			paras_html += f"""
		<div class="para-block">
			<p class="para-original">{original_highlighted}</p>
			<p class="para-translation">{translation}</p>
			{reveal_btn}
		</div>"""

		for img in images_by_para.get(i + 1, []):
			paras_html += image_figure(img)
		i += 1

	# build vocab summary
	vocab_summary = ""
	for v in vocab:
		_, badge_class, badge_label = VOCAB_TYPE_INFO.get(v["type"], VOCAB_TYPE_INFO["highfreq"])
		word_attr = htmllib.escape(v['word'], quote=True)
		pos = v.get("pos", "")
		pos_html = f'<span class="vocab-pos">{htmllib.escape(pos)}</span>' if pos else ""
		vocab_summary += f"""
			<div class="vocab-card" data-word="{word_attr}" onclick="showPopup(this.dataset.word)">
				<span class="vocab-word">{htmllib.escape(v['word'])}</span>
				<span class="badge {badge_class}">{badge_label}</span>{pos_html}
				<span class="vocab-ipa">{htmllib.escape(v['ipa'])}</span>
				<span class="vocab-def">{htmllib.escape(v['definition_zh'])}</span>
			</div>"""

	title_html = htmllib.escape(data["title"])
	source_html = htmllib.escape(data["source_name"])
	url_html = htmllib.escape(data["url"], quote=True)
	date_str = data["date"]

	tag = data.get("tag", "")
	tag_html = f'<span class="tag-badge">{htmllib.escape(tag)}</span>' if tag else ""

	bumps_html = build_bumps_html(data)
	scan_panel_html, scan_answers_html = build_scan_questions_html(data)

	summary_zh = data.get("summary_zh", "")
	summary_zh_html = ""
	if summary_zh:
		summary_zh_html = f"""
	<div class="summary-zh">
		<div class="section-label">整篇摘要</div>
		<p>{htmllib.escape(summary_zh)}</p>
	</div>"""

	related = pick_related(entry_filename(data), tag, all_entries, n=3)
	related_html = ""
	if related:
		related_cards = "".join(f"""
			<a class="related-card" href="{htmllib.escape(e['filename'], quote=True)}">
				<span class="related-source">{htmllib.escape(e['source_name'])}</span>
				<span class="related-title">{htmllib.escape(e['title'])}</span>
			</a>""" for e in related)
		related_html = f"""
<div class="related-section content-block">
	<div class="section-label">延伸閱讀</div>
	<div class="related-grid">{related_cards}
	</div>
</div>"""

	quiz_html = ""
	if quiz:
		quiz_html = f"""
<div class="quiz-section content-block" id="quiz-section">
	<div class="section-label">單字測驗（{len(quiz)} 題）</div>
	<div class="quiz-progress" id="quiz-progress"></div>
	<div class="quiz-sentence" id="quiz-sentence"></div>
	<div class="quiz-options" id="quiz-options"></div>
	<div class="quiz-feedback" id="quiz-feedback"></div>
	<button class="quiz-next" id="quiz-next" onclick="nextQuiz()" style="display:none;">下一題</button>
	<div class="quiz-result" id="quiz-result"></div>
</div>"""

	return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title>{title_html} — AI English Daily</title>
	{SETTINGS_INIT_SCRIPT}
	<link rel="stylesheet" href="../style.css?v={CSS_VERSION}">
	<link rel="stylesheet" href="{WENKAI_FONT_CSS}">
	<script src="../assets/settings.js" defer></script>
</head>
<body data-stage="bumps">

{SETTINGS_PANEL_HTML}

<header class="site-header site-header--nav">
	<a href="../index.html">AI English Daily</a>
	<span class="breadcrumb-date">/ {date_str}</span>
	<a class="nav-vocab" href="../vocab.html">我的單字</a>
</header>

<div class="article-header content-block">
	<div class="article-source">{source_html}{tag_html}</div>
	<h1 class="article-title">{title_html}</h1>
	<div class="article-meta">
		{date_str} &nbsp;·&nbsp; <a href="{url_html}" target="_blank" rel="noopener">原文連結</a>
	</div>
</div>

<div class="stage-track" id="stage-track">
	<button class="stage-step" data-step="bumps" onclick="goStage('bumps')">① 凸點</button>
	<button class="stage-step" data-step="full" onclick="goStage('full')">② 全文</button>
	<button class="stage-step" data-step="verify" onclick="goStage('verify')">③ 驗證</button>
</div>

{bumps_html}
{scan_panel_html}

<div class="vocab-section content-block" id="vocab-section">
	<button class="vocab-toggle" onclick="toggleVocab(this)">
		<span>今日詞彙表</span><span class="toggle-arrow">▼</span>
	</button>
	<div class="vocab-grid" id="vocab-grid">
		{vocab_summary}
	</div>
</div>

<div class="article-body content-block" id="article-body">
	{paras_html}
	<div class="stage-actions stage-actions--end" id="to-verify">
		<button class="btn-primary" onclick="goStage('verify')">讀完了，顯示翻譯與解答 →</button>
	</div>
</div>

<div class="verify-block content-block" id="verify-block">
	{summary_zh_html}
	{scan_answers_html}
</div>
{quiz_html}
{related_html}

<div class="popup-overlay" id="popup-overlay" onclick="closePopupOnOverlay(event)">
	<div class="popup-card" id="popup-card">
		<button class="btn-close" onclick="closePopup()">×</button>
		<div class="popup-word" id="popup-word"></div>
		<div class="popup-ipa" id="popup-ipa"></div>
		<div class="popup-badge" id="popup-badge"></div>
		<div class="popup-pos" id="popup-pos"></div>
		<div class="popup-def" id="popup-def"></div>
		<div class="popup-example" id="popup-example"></div>
		<div class="popup-actions">
			<button class="btn-speak" id="btn-speak" onclick="speakWord()">發音</button>
			<button class="btn-anki" id="btn-anki" onclick="toggleAnki()">加入 Anki</button>
		</div>
	</div>
</div>

<script>
	const vocabData = {vocab_json};
	const vocabMap = {{}};
	vocabData.forEach(v => {{ vocabMap[v.word.toLowerCase()] = v; }});

	const articleMeta = {{
		title: {json.dumps(data["title"], ensure_ascii=False)},
		date: {json.dumps(date_str, ensure_ascii=False)},
		source: {json.dumps(data["source_name"], ensure_ascii=False)}
	}};

	const badgeInfo = {{
		highfreq: ["badge-highfreq", "高頻詞"],
		general: ["badge-general", "學習詞"],
		term: ["badge-term", "術語"],
		phrase: ["badge-phrase", "片語"],
	}};

	// ---- 三階段閱讀 ----
	// 階段只影響「這次瀏覽」，不寫進 localStorage —— 刻意的：如果記住上次
	// 選擇，跳過一次就等於永久關掉這個功能。
	function goStage(stage) {{
		document.body.setAttribute("data-stage", stage);
		document.querySelectorAll(".stage-step").forEach(b => {{
			b.classList.toggle("is-active", b.dataset.step === stage);
		}});
		window.scrollTo({{ top: 0, behavior: "smooth" }});
	}}

	function revealPara(btn) {{
		btn.closest(".para-block").classList.add("revealed");
	}}

	// ---- Anki 單字收集 ----
	const ANKI_KEY = {json.dumps(ANKI_STORAGE_KEY)};

	function loadDeck() {{
		try {{ return JSON.parse(localStorage.getItem(ANKI_KEY) || "[]"); }}
		catch (e) {{ return []; }}
	}}

	function saveDeck(deck) {{
		try {{ localStorage.setItem(ANKI_KEY, JSON.stringify(deck)); }}
		catch (e) {{ alert("瀏覽器儲存空間已滿，無法再加入單字。"); }}
	}}

	function inDeck(word) {{
		return loadDeck().some(c => c.word.toLowerCase() === word.toLowerCase());
	}}

	function toggleAnki() {{
		if (!currentWord) return;
		const entry = vocabMap[currentWord.toLowerCase()];
		if (!entry) return;
		let deck = loadDeck();
		const idx = deck.findIndex(c => c.word.toLowerCase() === currentWord.toLowerCase());
		if (idx >= 0) {{
			deck.splice(idx, 1);
		}} else {{
			deck.push({{
				word: entry.word,
				pos: entry.pos || "",
				ipa: entry.ipa || "",
				definition_zh: entry.definition_zh || "",
				example: entry.example || "",
				article: articleMeta.title,
				date: articleMeta.date,
				added: new Date().toISOString().slice(0, 10)
			}});
		}}
		saveDeck(deck);
		syncAnkiButton();
		markCollectedWords();
	}}

	function syncAnkiButton() {{
		const btn = document.getElementById("btn-anki");
		if (!currentWord) return;
		const has = inDeck(currentWord);
		btn.textContent = has ? "已加入 ✓" : "加入 Anki";
		btn.classList.toggle("is-added", has);
	}}

	function markCollectedWords() {{
		const deck = loadDeck().map(c => c.word.toLowerCase());
		document.querySelectorAll("[data-word]").forEach(el => {{
			el.classList.toggle("is-collected", deck.includes(el.dataset.word.toLowerCase()));
		}});
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

		const [badgeClass, badgeLabel] = badgeInfo[entry.type] || badgeInfo.highfreq;
		const badge = document.getElementById("popup-badge");
		badge.className = "popup-badge";
		badge.innerHTML = `<span class="badge ${{badgeClass}}">${{badgeLabel}}</span>`;

		document.getElementById("popup-pos").textContent = entry.pos || "";
		syncAnkiButton();

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
		speechSynthesis.cancel();
		const utter = new SpeechSynthesisUtterance(currentWord);
		utter.lang = "en-US";
		speechSynthesis.speak(utter);
	}}

	function toggleVocab(btn) {{
		const grid = document.getElementById("vocab-grid");
		grid.classList.toggle("open");
		btn.querySelector(".toggle-arrow").textContent = grid.classList.contains("open") ? "▲" : "▼";
	}}

	// wire up highlighted words
	document.querySelectorAll(".word-highfreq, .word-term, .word-general, .word-phrase").forEach(el => {{
		el.addEventListener("click", () => showPopup(el.dataset.word));
	}});

	// close on Escape
	document.addEventListener("keydown", e => {{ if (e.key === "Escape") closePopup(); }});

	markCollectedWords();
	goStage("bumps");

	// quiz
	const quizData = {quiz_json};
	let quizIndex = 0;
	let quizScore = 0;
	let quizAnswered = false;

	function renderQuiz() {{
		if (quizIndex >= quizData.length) {{
			document.getElementById("quiz-sentence").style.display = "none";
			document.getElementById("quiz-options").style.display = "none";
			document.getElementById("quiz-feedback").textContent = "";
			document.getElementById("quiz-next").style.display = "none";
			document.getElementById("quiz-progress").textContent = "";
			document.getElementById("quiz-result").textContent = `答對 ${{quizScore}} / ${{quizData.length}} 題`;
			return;
		}}
		quizAnswered = false;
		const q = quizData[quizIndex];
		document.getElementById("quiz-progress").textContent = `第 ${{quizIndex + 1}} / ${{quizData.length}} 題`;
		document.getElementById("quiz-sentence").textContent = q.sentence;
		document.getElementById("quiz-feedback").textContent = "";
		document.getElementById("quiz-next").style.display = "none";
		document.getElementById("quiz-result").textContent = "";
		const optionsEl = document.getElementById("quiz-options");
		optionsEl.innerHTML = "";
		q.options.forEach(opt => {{
			const optBtn = document.createElement("button");
			optBtn.className = "quiz-option";
			optBtn.textContent = opt;
			optBtn.onclick = () => answerQuiz(opt, optBtn);
			optionsEl.appendChild(optBtn);
		}});
	}}

	function escapeHtml(s) {{
		const div = document.createElement("div");
		div.textContent = s;
		return div.innerHTML;
	}}

	function answerQuiz(selected, btn) {{
		if (quizAnswered) return;
		quizAnswered = true;
		const q = quizData[quizIndex];
		const correct = selected === q.answer;
		if (correct) quizScore++;
		document.querySelectorAll(".quiz-option").forEach(b => {{
			b.disabled = true;
			if (b.textContent === q.answer) b.classList.add("correct");
			else if (b === btn) b.classList.add("wrong");
		}});

		const resultLine = correct ? "答對了！" : `答錯了，正確答案：${{escapeHtml(q.answer)}}`;
		const zhLine = q.sentence_zh
			? `<div class="quiz-feedback-zh">${{escapeHtml(q.sentence_zh)}}</div>`
			: "";
		const optionsList = q.options.map(opt => {{
			const entry = vocabMap[opt.toLowerCase()];
			const def = entry ? entry.definition_zh : "";
			const isAnswer = opt === q.answer;
			return `<li class="quiz-feedback-option${{isAnswer ? " is-answer" : ""}}">`
				+ `<span class="quiz-feedback-word">${{escapeHtml(opt)}}</span>`
				+ (def ? ` — ${{escapeHtml(def)}}` : "")
				+ `</li>`;
		}}).join("");

		document.getElementById("quiz-feedback").innerHTML =
			`<div class="quiz-feedback-result">${{resultLine}}</div>`
			+ zhLine
			+ `<ul class="quiz-feedback-options">${{optionsList}}</ul>`;
		document.getElementById("quiz-next").style.display = "inline-block";
	}}

	function nextQuiz() {{
		quizIndex++;
		renderQuiz();
	}}

	if (quizData.length) renderQuiz();
</script>

</body>
</html>"""


def render_index(entries):
	"""entries: build_entry() 產生的 dict 清單，已依日期由新到舊排好。"""
	if entries:
		items = "\n".join(
			f'\t\t\t<li><a href="articles/{e["filename"]}">{e["date"]} — '
			f'{htmllib.escape(e["title"])} '
			f'<span class="source-tag">{htmllib.escape(e["source_name"])}</span></a></li>'
			for e in entries
		)
	else:
		items = '\t\t\t<li class="empty-state">尚無文章</li>'

	return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title>AI English Daily</title>
	{SETTINGS_INIT_SCRIPT}
	<link rel="stylesheet" href="style.css?v={CSS_VERSION}">
	<link rel="stylesheet" href="{WENKAI_FONT_CSS}">
	<script src="assets/settings.js" defer></script>
</head>
<body>

{SETTINGS_PANEL_HTML}

<header class="site-header">
	<h1>AI English Daily</h1>
	<p>每天一篇 AI 與科技英文長文，三階段引導閱讀 + 單字收集</p>
	<a class="nav-vocab" href="vocab.html">我的單字 →</a>
</header>

<div class="container">
	<div class="section-label">文章列表</div>
	<ul id="article-list">
{items}
	</ul>
</div>

</body>
</html>
"""


def render_vocab_page():
	"""「我的單字」頁。內容完全來自瀏覽器 localStorage，沒有伺服器端資料 ——
	所以這頁不需要在每次發文時重新產生內容，只是要跟著模板一起更新。"""
	return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title>我的單字 — AI English Daily</title>
	{SETTINGS_INIT_SCRIPT}
	<link rel="stylesheet" href="style.css?v={CSS_VERSION}">
	<link rel="stylesheet" href="{WENKAI_FONT_CSS}">
	<script src="assets/settings.js" defer></script>
</head>
<body>

{SETTINGS_PANEL_HTML}

<header class="site-header site-header--nav">
	<a href="index.html">AI English Daily</a>
	<span class="breadcrumb-date">/ 我的單字</span>
</header>

<div class="container">
	<div class="deck-toolbar">
		<div class="deck-count" id="deck-count"></div>
		<div class="deck-actions">
			<button class="btn-primary" onclick="exportDeck()">匯出給 Anki</button>
			<button class="btn-quiet" onclick="clearDeck()">全部清空</button>
		</div>
	</div>

	<details class="deck-help">
		<summary>第一次匯入 Anki 要怎麼設定</summary>
		<ol>
			<li>按「匯出給 Anki」下載 <code>anki-vocab.txt</code>（TSV 格式）。</li>
			<li>Anki 選「檔案 → 匯入」，挑這個檔案。</li>
			<li>欄位分隔選 <strong>Tab</strong>，勾選「允許在欄位中使用 HTML」。</li>
			<li>欄位順序是：單字 / 音標 / 詞性 / 中文 / 例句 / 出處。對應到你的筆記類型後按匯入。</li>
			<li>之後 Anki 會記住這組設定，下次直接選檔案就好。</li>
		</ol>
		<p class="deck-note">單字存在這台瀏覽器裡，換裝置或清除瀏覽資料就會消失，記得定期匯出。</p>
	</details>

	<div class="deck-list" id="deck-list"></div>
</div>

<script>
	const ANKI_KEY = {json.dumps(ANKI_STORAGE_KEY)};

	function loadDeck() {{
		try {{ return JSON.parse(localStorage.getItem(ANKI_KEY) || "[]"); }}
		catch (e) {{ return []; }}
	}}

	function saveDeck(deck) {{
		localStorage.setItem(ANKI_KEY, JSON.stringify(deck));
	}}

	function escapeHtml(s) {{
		const div = document.createElement("div");
		div.textContent = s == null ? "" : s;
		return div.innerHTML;
	}}

	function render() {{
		const deck = loadDeck();
		document.getElementById("deck-count").textContent =
			deck.length ? `收集了 ${{deck.length}} 個單字` : "還沒有收集任何單字";

		const list = document.getElementById("deck-list");
		if (!deck.length) {{
			list.innerHTML = '<p class="empty-state">在文章裡點開單字彈窗，按「加入 Anki」就會出現在這裡。</p>';
			return;
		}}
		list.innerHTML = deck.map((c, i) => `
			<div class="deck-card">
				<button class="deck-remove" onclick="removeCard(${{i}})" aria-label="移除">×</button>
				<div class="deck-word">${{escapeHtml(c.word)}}</div>
				<div class="deck-meta">${{escapeHtml(c.ipa)}} ${{escapeHtml(c.pos)}}</div>
				<div class="deck-def">${{escapeHtml(c.definition_zh)}}</div>
				<div class="deck-example">${{escapeHtml(c.example)}}</div>
				<div class="deck-source">${{escapeHtml(c.date)}} · ${{escapeHtml(c.article)}}</div>
			</div>`).join("");
	}}

	function removeCard(i) {{
		const deck = loadDeck();
		deck.splice(i, 1);
		saveDeck(deck);
		render();
	}}

	function clearDeck() {{
		if (!confirm("確定要清空全部收集的單字嗎？這個動作沒辦法復原。")) return;
		saveDeck([]);
		render();
	}}

	// TSV 的欄位分隔是 tab、列分隔是換行，所以欄位內容裡的 tab / 換行必須先
	// 換掉，否則一個例句就能把整張表的欄位錯開。
	function tsvCell(s) {{
		return String(s == null ? "" : s).replace(/[\\t\\r\\n]+/g, " ").trim();
	}}

	function exportDeck() {{
		const deck = loadDeck();
		if (!deck.length) {{ alert("還沒有收集任何單字。"); return; }}
		const rows = deck.map(c => [
			c.word, c.ipa, c.pos, c.definition_zh, c.example,
			`${{c.date}} ${{c.article}}`
		].map(tsvCell).join("\\t"));
		const blob = new Blob([rows.join("\\n")], {{ type: "text/plain;charset=utf-8" }});
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = "anki-vocab.txt";
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		URL.revokeObjectURL(a.href);
	}}

	render();
</script>

</body>
</html>
"""
