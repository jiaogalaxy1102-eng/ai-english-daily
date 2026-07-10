import html as htmllib
import json
import re

CSS_VERSION = 6


def highlight(text, vocab_map):
	# sort by length descending to match longer phrases first
	all_words = sorted(vocab_map.keys(), key=len, reverse=True)
	for word in all_words:
		entry = vocab_map[word]
		css_class = "word-highfreq" if entry["type"] == "highfreq" else "word-term"
		word_attr = htmllib.escape(entry["word"], quote=True)
		pattern = re.compile(re.escape(word), re.IGNORECASE)
		escaped_word = lambda m, c=css_class, a=word_attr: (
			f'<span class="{c}" data-word="{a}">'
			f'{m.group()}</span>'
		)
		text = pattern.sub(escaped_word, text, count=1)
	return text


def render_article(data):
	"""data: dict with keys date, title, source_name, url, paragraphs, images, vocab
	(see data/{date}.json for the on-disk shape)."""
	vocab = data["vocab"]
	vocab_map = {v["word"].lower(): v for v in vocab}

	# escape JSON for embedding in HTML — prevent </script> from breaking the tag
	vocab_json = json.dumps(vocab, ensure_ascii=False).replace("</script>", r"<\/script>")

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
	for i, para in enumerate(data["paragraphs"]):
		tag = para["tag"]
		original_highlighted = highlight(para["text"], vocab_map)
		translation = para.get("translation", "")

		if tag in ["h2", "h3"]:
			paras_html += f"""
		<div class="para-block heading-block">
			<{tag} class="para-original">{original_highlighted}</{tag}>
			<{tag} class="para-translation">{translation}</{tag}>
		</div>"""
		else:
			paras_html += f"""
		<div class="para-block">
			<p class="para-original">{original_highlighted}</p>
			<p class="para-translation">{translation}</p>
		</div>"""

		# insert images after this paragraph
		# after_paragraph is recorded as "how many paragraphs collected so far"
		# at scrape time, which is i+1 once this (i-th, 0-indexed) paragraph is added
		for img in images_by_para.get(i + 1, []):
			paras_html += image_figure(img)

	# build vocab summary
	vocab_summary = ""
	for v in vocab:
		badge_class = "badge-highfreq" if v["type"] == "highfreq" else "badge-term"
		badge_label = "高頻詞" if v["type"] == "highfreq" else "術語"
		word_attr = htmllib.escape(v['word'], quote=True)
		vocab_summary += f"""
			<div class="vocab-card" data-word="{word_attr}" onclick="showPopup(this.dataset.word)">
				<span class="vocab-word">{v['word']}</span>
				<span class="badge {badge_class}">{badge_label}</span>
				<span class="vocab-ipa">{v['ipa']}</span>
				<span class="vocab-def">{v['definition_zh']}</span>
			</div>"""

	title_html = htmllib.escape(data["title"])
	source_html = htmllib.escape(data["source_name"])
	url_html = htmllib.escape(data["url"], quote=True)
	date_str = data["date"]

	return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title>{title_html} — AI English Daily</title>
	<link rel="stylesheet" href="../style.css?v={CSS_VERSION}">
	<script>
		window.MathJax = {{
			tex: {{ inlineMath: [['\\\\(', '\\\\)']], displayMath: [['\\\\[', '\\\\]']] }}
		}};
	</script>
	<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js" defer></script>
</head>
<body>

<header class="site-header site-header--nav">
	<a href="../index.html">AI English Daily</a>
	<span class="breadcrumb-date">/ {date_str}</span>
</header>

<div class="article-header content-block">
	<div class="article-source">{source_html}</div>
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
		speechSynthesis.cancel();
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
	<link rel="stylesheet" href="style.css?v={CSS_VERSION}">
</head>
<body>

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
