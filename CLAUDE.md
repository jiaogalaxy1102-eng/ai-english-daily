# CLAUDE.md

本檔案提供 Claude Code 在此 repo 工作時的指引。

## 這是什麼

一個靜態網站(GitHub Pages,見 `.nojekyll`),每天發佈一篇中英對照(英文/繁體中文)的 AI 新聞文章,附互動式單字彈窗。除了下方的 Python 腳本沒有其他建置工具——`articles/` 的 HTML 和 `index.html` 都是直接 commit 進 repo 的。

已知的爬蟲陷阱、過去的 bug 與修法、已討論定案的設計決策都記在 [NOTES.md](NOTES.md)——重新調查問題或重新提案前先查它,遇到值得記住的事寫進去(不要寫到 Claude 的記憶系統)。

## 指令

```bash
pip install -r requirements.txt

# 產生今天的文章(需要 GEMINI_API_KEY 環境變數)
python generate.py

# 重新產生指定日期
DATE_OVERRIDE=2026-07-10 python generate.py

# 用現行模板重新渲染所有文章 + index.html(改過 template.py 之後執行,
# 讓舊文章也套用新設計)
python rebuild.py
```

沒有測試、linter 或建置步驟。

## 架構

內容與呈現拆成三段:

1. **`generate.py`**——每日流水線。從 `sources.json` 挑一個 RSS 來源,爬文章內文(`fetch_article_content`,用 BeautifulSoup),把段落送給 Gemini(`call_gemini`,模型由 `GEMINI_MODEL` 決定)做繁中翻譯 + 單字擷取,組成純 dict(`build_article_data`)寫入 `data/{date}.json`,再呼叫 `template.py` 渲染 `articles/{date}.html`,最後 `rebuild_index()` 從整個 `data/` 重建 `index.html`。
2. **`data/{date}.json`**——已發佈文章的唯一真實來源(source of truth):標題、來源、網址、`paragraphs`(原文 + 翻譯,依序)、`images`(含 `after_paragraph` 位置)、`vocab`(word/type/ipa/definition/example)。這裡的內容不做 HTML 跳脫、不預先標記單字——那是 `template.py` 的工作。
3. **`template.py`**——純渲染。`render_article(data)` 把一篇文章 dict 變成完整 HTML 頁(`highlight()` 標記單字、單字彈窗標記 + 行內 JS、圖片穿插);`render_index(entries)` 從 `{date, title, source_name, filename}` 清單產生 `index.html`。兩者同時被 `generate.py`(每日、增量)和 `rebuild.py`(整批,讀取 `data/` 全部檔案)呼叫。

**要改網站設計或文章版型**:改 `template.py`(和 `style.css`),然後跑 `python rebuild.py` 讓所有既有文章套用,不是只有未來的文章。GitHub Actions 也有手動觸發的 `Rebuild Site` workflow(`.github/workflows/rebuild.yml`)在 CI 做同樣的事。

### 單看一個檔案看不出來的事

- **`after_paragraph` 的索引規則**:`data/*.json` 裡圖片的 `after_paragraph` 值等於「遇到這張圖時已經爬了幾個段落」(1-indexed 計數,在 `fetch_article_content` 設定)。`render_article` 在 `enumerate(paragraphs)` 迴圈內用 `images_by_para.get(i + 1, [])` 消費它——`+1` 是為了對齊爬蟲的計數慣例和渲染端的 0-indexed 迴圈。改任一邊都要保持兩邊同步。
- **CSS 快取破壞**:`template.py` 有一個 `CSS_VERSION` 常數,同時用於 `articles/*.html`(`../style.css?v=N`)和 `index.html`(`style.css?v=N`)。每次改 `style.css` 都要把它加一,免得瀏覽器用舊快取——兩個模板必須維持同一版本號。
- **冪等 /「已產生過」判斷**:`generate.py` 的 `existing_dates()` 是掃 `articles/*.html`(不是 `data/*.json`)來判斷今天是否已產生。`data/` 和 `articles/` 必須永遠一起寫入——讓其中一邊超前,每日任務就會默默重產(或默默跳過)某一天。
- **`.github/workflows/daily.yml`** 依排程執行(cron,UTC 01:00 = 台灣 09:00),也可手動 `workflow_dispatch` 帶選填的 `date_override` 參數;跑完 `generate.py` 後把 `data/`、`articles/`、`index.html` 一起 commit。
- 單字在段落中的標記方式:每個字/片語只包第一個不分大小寫的符合處,包成 `<span class="word-highfreq">` 或 `<span class="word-term">`(見 `template.py` 的 `highlight()`);長片語優先於短單字比對(依長度遞減排序),避免片語內的短字先搶走符合。
