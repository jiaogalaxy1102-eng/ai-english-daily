# CLAUDE.md

本檔案提供 Claude Code 在此 repo 工作時的指引。

## 這是什麼

一個靜態網站(GitHub Pages,見 `.nojekyll`),每天發佈一篇 AI/科技領域的英文文章,用三階段引導閱讀的方式呈現,附互動式單字彈窗與 Anki 匯出。除了下方的 Python 腳本沒有其他建置工具——`articles/` 的 HTML、`index.html`、`vocab.html` 都是直接 commit 進 repo 的。

已知的爬蟲陷阱、過去的 bug 與修法、已討論定案的設計決策都記在 `NOTES.md`——重新調查問題或重新提案前先查它,遇到值得記住的事寫進去(不要寫到 Claude 的記憶系統)。

**`NOTES.md` 不進版控**(在 `.gitignore` 裡),只存在本機。全新 clone 不會有這個檔案,那是預期行為,不用去找也不用重建。

## 指令

```bash
pip install -r requirements.txt

# 產生今天的文章(需要 GEMINI_API_KEY 環境變數)
python generate.py

# 重新產生指定日期
DATE_OVERRIDE=2026-08-01 python generate.py

# 用現行模板重新渲染所有文章 + index.html + vocab.html
# (改過 template.py 之後執行,讓舊文章也套用新設計)
python rebuild.py

# 檢查來源健康度。要加新來源之前一定要先跑這個。
python check_source.py                    # 檢查現有 sources.json
python check_source.py <rss網址> [...]     # 檢查候選來源
```

沒有測試、linter 或建置步驟。

## 架構

內容與呈現拆成三段:

1. **`generate.py`**——每日流水線。從 `sources.json` 的所有來源收集未使用過的文章(`gather_candidates`),依序嘗試爬取(`fetch_article_content`)直到拿到夠長的一篇,把段落送給 Gemini(`call_gemini`,模型由 `GEMINI_MODEL` 決定)做繁中翻譯、單字擷取、摘要與掃讀問題,組成純 dict(`build_article_data`)寫入 `data/{date}.json`,再呼叫 `template.py` 渲染 `articles/{date}.html`,最後重建 `index.html` 與 `vocab.html`。
2. **`data/{date}.json`**——已發佈文章的唯一真實來源(source of truth):標題、來源、網址、`summary_en`/`summary_zh`、`conclusion_index`、`scan_questions`、`paragraphs`(原文 + 翻譯,依序)、`images`(含 `after_paragraph` 位置)、`vocab`。這裡的內容不做 HTML 跳脫、不預先標記單字——那是 `template.py` 的工作。
3. **`template.py`**——純渲染。`render_article(data)` 把一篇文章 dict 變成完整 HTML 頁;`render_index(entries)` 產生 `index.html`;`render_vocab_page()` 產生 `vocab.html`。三者同時被 `generate.py`(每日、增量)和 `rebuild.py`(整批)呼叫。

**要改網站設計或文章版型**:改 `template.py`(和 `style.css`),然後跑 `python rebuild.py` 讓所有既有文章套用,不是只有未來的文章。GitHub Actions 也有手動觸發的 `Rebuild Site` workflow(`.github/workflows/rebuild.yml`)在 CI 做同樣的事。

## 三階段閱讀

閱讀流程是這個網站的核心設計,不是裝飾:

1. **凸點(bumps)**——只顯示 AI 英文導讀 + 每段首句 + 標出的結論段。全英文,不出現中文。
2. **全文**——展開英文正文,翻譯藏起來,頂部釘住 3 題掃讀問題。個別段落可按「看中文」單獨展開。
3. **驗證**——顯示全部翻譯、中文摘要、掃讀問題解答。

實作上三個階段的內容全部渲染在同一頁,靠 `<body data-stage>` + CSS 決定哪些看得見(規則在 `style.css` 的「三階段引導閱讀」段落)。**翻譯預設隱藏是整個設計的重點**,不要為了「方便」把 `.para-translation { display: none }` 拿掉。

階段狀態刻意不存 localStorage:如果記住上次選擇,跳過一次就等於永久關掉這個功能。

## 單看一個檔案看不出來的事

- **`after_paragraph` 的索引規則**:`data/*.json` 裡圖片的 `after_paragraph` 值等於「遇到這張圖時已經爬了幾個段落」(1-indexed 計數,在 `extract_content` 設定)。`render_article` 在迴圈內用 `images_by_para.get(i + 1, [])` 消費它——`+1` 是為了對齊爬蟲的計數慣例和渲染端的 0-indexed 迴圈。改任一邊都要保持兩邊同步。另外 `trim_chrome` 修剪頭部段落後會用 `reindex_images` 平移這個值,三處必須一致。
- **CSS 快取破壞**:`template.py` 有一個 `CSS_VERSION` 常數,三個模板(文章頁、index、vocab)共用。每次改 `style.css` 都要把它加一,免得瀏覽器用舊快取。
- **冪等 /「已產生過」判斷**:`generate.py` 的 `existing_dates()` 是掃 `articles/*.html`(不是 `data/*.json`)來判斷今天是否已產生。`data/` 和 `articles/` 必須永遠一起寫入——讓其中一邊超前,每日任務就會默默重產(或默默跳過)某一天。
- **`.github/workflows/daily.yml`** 依排程執行(cron,UTC 01:00 = 台灣 09:00,一天一次),也可手動 `workflow_dispatch` 帶選填的 `date_override` 參數;跑完 `generate.py` 後把 `data/`、`articles/`、`index.html`、`vocab.html` 一起 commit。
- **單字標記方式**:每個字/片語只包第一個不分大小寫的符合處(見 `template.py` 的 `highlight()`);長片語優先於短單字比對(依長度遞減排序),避免片語內的短字先搶走符合。因為是字面比對,vocab 裡的 `word` 要用文章中實際出現的形態(例如文章寫 `spun out of control` 就不要填 `spin out of control`,否則標不到)。
- **首句是渲染時才算的**,不存進 JSON(`template.py` 的 `first_sentence()`)。演算法改了跑 `rebuild.py` 就好,不用重新生成資料。
- **Anki 單字存在 localStorage**(key 見 `template.py` 的 `ANKI_STORAGE_KEY`),文章頁負責加入、`vocab.html` 負責匯出。兩邊共用同一個 key,改名字要一起改,否則使用者已收集的單字會突然消失。匯出格式是 TSV(純前端做不出 `.apkg`)。
