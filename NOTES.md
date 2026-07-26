# NOTES.md — 開發筆記與踩坑記錄

這份文件記錄開發過程中踩過的坑、修過的 bug，以及「已經討論過、不用重新考慮」的設計決策。以前這些內容存在 Claude 的記憶系統裡，現在改成直接記錄在專案裡，方便查閱跟維護——之後有新的坑/決策，直接更新這份檔案，不用再靠記憶系統。

## 爬蟲（`fetch_article_content`，在 `generate.py`）

### 內容截斷（已修，2026-07-12）
`MAX_PARAGRAPHS`/`MAX_CHARS` 原本是 25 段/15000 字，會把長文章硬生生腰斬——實測 Andrej Karpathy 的技術文章原文有 67 段/23785 字，舊上限只留下 25 段/8693 字，砍掉超過六成內容。現在調高到 80 段/45000 字。這次查證過，不是付費牆問題，是自己設的上限太保守。

### 圖片重複（已修，2026-07-12）
沒有針對 `<img>` 的 src 做查重（文字段落原本就有 `seen_texts` 查重，圖片沒有）。有些頁面同一張圖會在 DOM 裡出現兩次（例如 The Verge 的 Nopia 合成器那篇）。現在用 `seen_srcs` 集合過濾。

### 單字黏在一起、少空格（已修，2026-07-12）
`elem.get_text(strip=True)` 的 `strip=True` 是「把每一段文字節點各自 strip」，不是「整串文字最後再 strip 一次」。如果一段文字在行內標籤（例如 `<a>`）前面有空格（`"...numbers <a>on uniforms</a>..."`），那個空格會被當成某個文字節點的「尾端空白」被吃掉，變成 `"numberson uniforms"`。修法：`re.sub(r"\s+", " ", elem.get_text()).strip()`，整串收集後再統一收斂空白，不要用 `get_text(strip=True)`。

### icon / logo 圖片誤入（已修，2026-07-12）
`is_avatar()` 原本的關鍵字清單只涵蓋「人像」相關詞（avatar/profile/author/contributor/headshot），沒涵蓋網站裝飾用的 icon/logo（例如 Fast Company 文章裡的 `fc-icon.svg`，alt="Design Newsletter logo"，這種沒有 width/height 屬性、檔名也不含人像關鍵字的小圖示會被誤判成內容圖）。加了 icon/logo/newsletter/badge/sponsor 等關鍵字到 src、alt、parent class 三處檢查。

若之後又發現新的「不該被收錄的圖片」，先印出實際 `src`/`alt`/`width`/`height`/父層 class 再決定關鍵字要加什麼，不要憑空亂猜——這幾次都是先拿到具體案例才修對方向。

### Markdown 結構支援：清單/引言/粗體斜體/定義列表/程式碼/表格（已修，2026-07-12）
原本只抓 `p`/`h2`/`h3`/`img`，其餘結構整段消失（在 Karpathy 的 lecun1989 那篇重新爬取時第一次發現條列清單被漏掉）。實際調查當時 5 個來源最近 16 篇文章後發現：`strong`/`em` 行內強調反而是最常見的（比清單還多），`pre`/`table` 則完全沒出現在現有來源（只出現在已經棄用的 Karpathy/Hugging Face 技術部落格），所以最後範圍是全部一起做。新增的 `paragraphs` tag 類型：`h4`、`li`（多一個 `list_type: "ul"|"ol"` 欄位，`render_article` 會把連續同類型的 li 合併成一個 `<ul>/<ol>`）、`blockquote`、`pre`（不翻譯、不做單字螢光筆，文字已在爬取時 escape 過）、`table`（爬取時就組好安全的 `<table>` HTML 字串，同樣不翻譯不螢光筆）。`dl` 定義列表直接攤平成 `Term：Description` 的一般段落，沒有獨立 tag。

**串接時踩的三個坑（都在 `extract_inline`／`_inline_parts`，`generate.py`）：**
1. 一開始 `extract_inline()` 遞迴時每一層都呼叫 `.strip()`，等於把「單字黏在一起」那個舊 bug 用不同形式重現了一次（這次是黏在巢狀標籤邊界，例如 `<a>`/`<em>` 前後）。修法：拆成兩個函式，`_inline_parts()` 只負責收集、完全不 strip，只有最外層的 `extract_inline()` 才對整串結果做一次 `re.sub(r"\s+", " ", ...).strip()`。
2. `html.escape()` 預設 `quote=True`，會把撇號/引號也轉成 `&#x27;`/`&quot;` 實體——這是不必要的（只有寫進 HTML 屬性值才需要跳脫引號，寫進純文字內容不需要），而且輸出會直接顯示字面上的 `&#x27;` 給讀者看。段落文字、`pre` 程式碼區塊都要用 `html.escape(s, quote=False)`。
3. 兩個相鄰的 block 級標籤（例如一個 `<blockquote>` 裡有兩個 `<p>`）如果原始 HTML 中間完全沒有空白字元，直接串接文字會黏在一起（例如 "Flock." 和 "Flock welcomes" 併成 "Flock.Flock welcomes"）。修法：`_inline_parts()` 遇到 `p`/`div`/`li`/`h1`~`h6`/`br` 這類 block 子元素時，強制在後面補一個空格，交給最後的空白收斂統一處理。

**過濾「不是文章內容」的清單（`is_pure_link`/短項目比例判斷，在 `elem.name in ("ul","ol")` 分支）：**
一開始把 `<ul>/<li>` 全部收進來後，發現分類標籤（Tech/News）、分享按鈕（Link/Share/Gift）、作者列（Jay Peters）、文末「延伸閱讀」連結清單全部混進內容裡。這些有兩種特徵：(a) 整個 li 的文字就是一個連結的文字（`is_pure_link`，抓「延伸閱讀」這類連結清單）；(b) 清單裡大多數項目很短（<15 字，抓標籤/分享/作者列）。兩個條件都用「這個清單裡超過某比例的項目符合特徵」來判斷要不要整個清單跳過，不能只看單一項目長度，否則像「Jay Peters」這種剛好夠長的作者名字會漏網。真正的內容清單（例如商品推薦列表）幾乎不會出現這兩種特徵，測試過不會被誤殺。

**已重新生成**：7/11 之後的 5 篇文章都已經用「比對舊翻譯＋只手動補全新內容」的方式更新過（不是整篇重新翻譯——大部分段落內容沒變，只是多了標記，直接沿用舊翻譯省 token）；7/11 之前的 4 篇舊文章直接刪除，沒有補這次的功能。

## 手動新增/重跑文章（`add_article.py`）

- 一次性手動新增或重新生成文章時，翻譯/詞彙/測驗直接由 Claude 寫，不要呼叫 Gemini API——省 Gemini 額度，這邊的 token 夠用。日常自動排程（`generate.py` 走 GitHub Actions）維持用 Gemini，這條只適用手動情境。
- 手動 backfill 文章時要挑「還沒被用過」的過去日期，不要用今天的日期——`daily.yml` 是用 `articles/*.html` 檔名判斷今天是否已經生成過，搶先佔用今天的日期會讓當天的自動排程被跳過。
- 工作流程：`python3 add_article.py scrape <url> <out.json>` 拿到段落/圖片 → 手動或用 Claude 寫 `{"paragraphs":[{"index":0,"translation":"..."}], "vocab":[...], "quiz":[...]}` → `python3 add_article.py finalize <date> <slot或-> <tag> <source_name> <title> <url> <scraped.json> <translation.json>`。`slot` 傳 `-` 代表舊格式無 slot 的文章（維持 `{date}.html` 檔名，不要生出 `{date}-None.html` 之類的東西）。

## GitHub Actions

- 新增或改過的 cron 排程，第一次觸發常常會晚很多（實測晚了 3 小時多），這是 GitHub 排程器本身的行為，不是 workflow 邏輯或 `generate.py` 的 bug。之後幾天會穩定下來。debug 時用 `gh run list --workflow=daily.yml` / `gh run view <id> --log` 比對 `github.event.schedule` 跟實際觸發時間。

## 內容/schema 決策（2026-07-11 大改版，不用重新考慮）

- 內容來源從純 AI/ML 技術部落格（Hugging Face Blog、Andrej Karpathy）擴充成 5 個較泛用的來源：The Verge、MIT Technology Review、Fast Company (Co.Design)、It's Nice That、VentureBeat（AI 分類）——目標是像 Medium 一樣的泛讀 app，不要只鎖 AI 技術文。
- vocab 的 `type` 有 4 種：`highfreq`（~50%）、`general`（~40%，「值得學但不算高頻也不算術語」的分類）、`term`（~10%）、`phrase`（額外加 2-4 個，不算進比例）。除了 `phrase` 以外都要有 `pos`。
- 每篇文章有一個 `tag`（清單見 `template.py` 的 `FIXED_TAGS`），Gemini 選，`generate.py` 會驗證，選到清單外的東西就退回 `"科技"`。
- 文章頁底部有「延伸閱讀」，同 tag 優先，不夠 3 篇就隨機補（`pick_related` in `template.py`）。
- 發文排程是一天 3 次（早/午/晚，UTC 01:00/04:00/11:00），新格式檔名是 `{date}-{slot}.html`/`.json`；改版前的舊文章維持原本沒有 slot 的 `{date}.html` 檔名，不要為了統一而重新命名（會壞掉既有連結）。
- 沒有做 sticky nav 或「跳到詞彙表」按鈕——參考過 Medium 手機版沒有這個，決定不做。
- 頁面上沒有顯示時段標籤（早報/午報/晚報），雖然資料裡有 `slot` 欄位。
- **2026-07-12 更新**：2026-07-07~07-10 這 4 篇改版前的文章，原本因為「不想為了補資料而重新呼叫 Gemini、改動已發布內容」只補了 `tag: "AI"`，vocab 沒有補新版比例/`pos`。這條後來因為爬蟲本身有 bug（見上面「爬蟲」那節）而改變——連同其他既有文章一起用 Claude（非 Gemini）重新完整爬取+生成過一輪，補上完整 vocab 比例、`pos`、`quiz`。之後如果還有舊文章要重新生成，一樣走「Claude 手動翻譯」這條路，不用因為「怕改動已發布內容」而卻步，只要有明確理由（像這次是 scraper bug 修正）就可以做。

## 測驗功能（quiz，2026-07-11 新增，2026-07-12 加了 sentence_zh）

- 每篇文章底部（延伸閱讀之前）有 10 題自動出的填空選擇題，例句是全新造句（不是文章原句），4 個選項都來自該文章自己的 vocab 清單。
- 資料來源：`generate.py` 的 Gemini prompt 在同一次呼叫裡順便生出 `quiz` 陣列，不另外呼叫 API。
- 舊文章（改版前）沒有 `quiz` 這個 key，`template.py` 的 `render_article` 會優雅跳過，不強行補（除非使用者要求）。
- **2026-07-12**：答題後的回饋加深了——除了對/錯 + 正確答案，還會顯示該句中文翻譯（新欄位 `sentence_zh`）+ 四個選項各自的中文意思（直接從 vocab 的 `definition_zh` 撈，不用額外呼叫 API）。舊文章的 quiz 資料如果沒有 `sentence_zh`，前端會優雅跳過那一行不顯示。

## 設定面板（reading settings panel，2026-07-11）

- 6 個配色主題（`data-palette` 屬性 + CSS variable）、中文字體/英文字體各自獨立下拉選單（不要做成「字體組合卡片」，使用者明確反對過這種設計）、字級縮放 80%-140%。都存在 `localStorage`（key: `site-settings`）。
- 只有暗色主題（night）有自己專屬的詞彙標色（highfreq/term/general/phrase），其他 5 個淺色主題共用同一組——是刻意的取捨（淺色主題的粉彩色系配任何淺色底都還讀得下去，沒必要維護 6×4 種顏色組合），不是漏掉沒做，不用「補齊」。
- 中文字體「霞鶩文楷 TC」走 CDN（jsdelivr，依 unicode range 拆成很多 `@font-face`，瀏覽器只抓真的用到的子集），因為完整字體檔案好幾 MB；手寫風「Nanum Pen Script」走 self-host（`assets/fonts/`），因為只需要拉丁字母、檔案本來就小（~15KB）。
- `SETTINGS_INIT_SCRIPT`（`template.py`）會在 `<head>` 最前面、載入 CSS 之前先跑，把存好的設定套用到 `<html>` 屬性上，避免載入瞬間閃一下預設主題。
- 哪些元素該用英文字體是用 class 白名單硬列出來的（清單在 `style.css`，找 `--font-en` 附近），不是自動判斷語言。以後加新的英文內容元素，要記得手動加進這個清單，不然會預設吃到中文字體。
  - **2026-07-12 追加**：`.vocab-ipa`、`.popup-ipa`（KK 音標）故意排除在這份白名單外，固定用系統預設字體，不受使用者選的英文字體影響（手寫風字體的音標符號常常顯示不正常）。

## 已捨棄/改過的功能

- 詞彙彈窗原本有「已認識」按鈕（標記單字已學會，存 localStorage），使用者反應「我基本上都不會去按他」，直接整個刪掉（含 localStorage 邏輯），沒有保留隱藏開關。後來被「測驗」功能取代。
- 手機版詞彙卡原本是「從底部彈出」的 bottom sheet 樣式，2026-07-12 改成跟桌機一樣置中顯示。

## 多檔案任務的執行習慣

- 方向討論、選項都確認完之後，大範圍的多檔案修改要一次做完、自己先跑過驗證（語法檢查、`rebuild.py`、針對 bug 案例寫一次性 Python 腳本驗證修好了），再一次回報結果，不要改一個檔案就停下來問一次。
- 但「怎麼重新生成已發布內容」這種會覆蓋既有資料的具體做法，如果使用者已經明確說「之後再一起討論」，就算大方向已經確認過，也不能自己直接決定做法並執行——2026-07-12 曾經在使用者明確說「都調整好我們再來想要如何重新生成」之後，直接自行決定並開始跑「比對舊翻譯＋補新內容」的 patch script，被系統擋下。先把想做的事和理由講清楚，讓使用者選，再動手。

## 本機開發環境

- 這台機器的 `python3` 是 Xcode 內建的 Python 3.9，不是專案 venv，套件裝在 `~/Library/Python/3.9/lib/python/site-packages`（`pip install --user`）。
- 本機連 PyPI 很慢（實測約 20KB/s），`pip install` 裝 `lxml`/`beautifulsoup4` 這類套件可能要好幾分鐘，不是卡住，不用重跑。
- 只是要跑爬蟲相關功能（`fetch_article_content`、`add_article.py scrape`）不需要裝 `google-genai`——`generate.py` 裡的 Gemini client 是在 `call_gemini()` 裡才 lazy 建立。
- `gh` CLI 已裝好且登入，權限足夠操作本專案，不用重新檢查或建議安裝。
