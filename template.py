import html as htmllib
import json
import random
import re

CSS_VERSION = 11

FIXED_TAGS = ["AI", "科技", "商業與新創", "科學", "社會與文化", "生活與心理", "觀點評論"]

SLOT_ORDER = {"morning": 0, "noon": 1, "evening": 2}

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


def entry_filename(d):
	"""Reconstruct an article's HTML filename from its data dict.
	Articles generated before the multi-slot schedule have no "slot" key
	and keep the plain {date}.html filename they were already published under."""
	slot = d.get("slot")
	return f"{d['date']}-{slot}.html" if slot else f"{d['date']}.html"


def build_entry(d):
	return {
		"date": d["date"],
		"slot": d.get("slot"),
		"title": d["title"],
		"source_name": d["source_name"],
		"filename": entry_filename(d),
		"tag": d.get("tag", ""),
	}


def sort_entries(entries):
	"""Newest first; within the same date, evening > noon > morning."""
	return sorted(
		entries,
		key=lambda e: (e["date"], SLOT_ORDER.get(e.get("slot"), -1)),
		reverse=True,
	)


def pick_related(current_filename, current_tag, all_entries, n=3):
	others = [e for e in all_entries if e["filename"] != current_filename]
	same_tag = [e for e in others if current_tag and e.get("tag") == current_tag]
	chosen = same_tag[:n]
	if len(chosen) < n:
		pool = [e for e in others if e not in chosen]
		random.shuffle(pool)
		chosen += pool[: n - len(chosen)]
	return chosen


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


def render_article(data, all_entries=None):
	"""data: dict with keys date, title, source_name, url, paragraphs, images, vocab
	(see data/{date}.json for the on-disk shape).
	all_entries: list of entries (see build_entry) for every published article,
	used to populate the "related articles" section at the bottom of the page."""
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
				<li>{highlight(p["text"], vocab_map)}<span class="li-translation">{p.get("translation", "")}</span></li>"""
				for img in images_by_para.get(i + 1, []):
					items_html += image_figure(img)
				i += 1
			paras_html += f"""
		<div class="para-block list-block">
			<{list_type} class="para-original">{items_html}
			</{list_type}>
		</div>"""
			continue

		if tag in ("h2", "h3", "h4"):
			original_highlighted = highlight(para["text"], vocab_map)
			translation = para.get("translation", "")
			paras_html += f"""
		<div class="para-block heading-block">
			<{tag} class="para-original">{original_highlighted}</{tag}>
			<{tag} class="para-translation">{translation}</{tag}>
		</div>"""
		elif tag == "blockquote":
			original_highlighted = highlight(para["text"], vocab_map)
			translation = para.get("translation", "")
			paras_html += f"""
		<div class="para-block quote-block">
			<blockquote class="para-original">{original_highlighted}</blockquote>
			<blockquote class="para-translation">{translation}</blockquote>
		</div>"""
		elif tag == "pre":
			# code isn't translated or vocab-highlighted; text is already
			# HTML-escaped at scrape time (see fetch_article_content)
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
			translation = para.get("translation", "")
			paras_html += f"""
		<div class="para-block">
			<p class="para-original">{original_highlighted}</p>
			<p class="para-translation">{translation}</p>
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
				<span class="vocab-word">{v['word']}</span>
				<span class="badge {badge_class}">{badge_label}</span>{pos_html}
				<span class="vocab-ipa">{v['ipa']}</span>
				<span class="vocab-def">{v['definition_zh']}</span>
			</div>"""

	title_html = htmllib.escape(data["title"])
	source_html = htmllib.escape(data["source_name"])
	url_html = htmllib.escape(data["url"], quote=True)
	date_str = data["date"]

	tag = data.get("tag", "")
	tag_html = f'<span class="tag-badge">{htmllib.escape(tag)}</span>' if tag else ""

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
	<script>
		window.MathJax = {{
			tex: {{ inlineMath: [['\\\\(', '\\\\)']], displayMath: [['\\\\[', '\\\\]']] }}
		}};
	</script>
	<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js" defer></script>
</head>
<body>

{SETTINGS_PANEL_HTML}

<header class="site-header site-header--nav">
	<a href="../index.html">AI English Daily</a>
	<span class="breadcrumb-date">/ {date_str}</span>
</header>

<div class="article-header content-block">
	<div class="article-source">{source_html}{tag_html}</div>
	<h1 class="article-title">{title_html}</h1>
	<div class="article-meta">
		{date_str} &nbsp;·&nbsp; <a href="{url_html}" target="_blank" rel="noopener">原文連結</a>
	</div>
</div>

<div class="vocab-section content-block">
	<button class="vocab-toggle" onclick="toggleVocab(this)">
		<span>今日詞彙表</span><span class="toggle-arrow">▼</span>
	</button>
	<div class="vocab-grid" id="vocab-grid">
		{vocab_summary}
	</div>
</div>

<div class="article-body content-block">
	{paras_html}
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
		</div>
	</div>
</div>

<script>
	const vocabData = {vocab_json};
	const vocabMap = {{}};
	vocabData.forEach(v => {{ vocabMap[v.word.toLowerCase()] = v; }});

	const badgeInfo = {{
		highfreq: ["badge-highfreq", "高頻詞"],
		general: ["badge-general", "學習詞"],
		term: ["badge-term", "術語"],
		phrase: ["badge-phrase", "片語"],
	}};

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
	"""entries: list of dicts with keys date, title, source_name, filename,
	already sorted newest-first."""
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
	<p>每日一篇 AI 領域英文文章，雙語對照 + 互動詞彙學習</p>
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
