"""RSS 來源健檢。

把踩過的坑變成自動檢查，決定一個來源能不能收進 sources.json 之前先跑這個。

用法：
    python check_source.py                      # 檢查 sources.json 目前收的來源
    python check_source.py <rss網址> [<rss網址>...]   # 檢查候選來源

檢查項目（每一項都對應一個真的踩過的坑）：
  1. RSS 還活著嗎、抓得到幾篇        —— VentureBeat 的 RSS 死掉半個月沒人發現
  2. RSS 有沒有附全文                —— 頁面被擋時的備援來源
  3. 文章頁會不會被擋（403 / 429）   —— Fast Company、Engadget、OpenAI 都會擋
  4. 爬到的內容 vs RSS 全文差多少    —— Fast Company 只吐得出前 34%
  5. 平均每段字數                    —— 正常 200-400；太高代表抓到雜訊
                                         （Wired 的產品導購文是 1671）
"""
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import feedparser
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from generate import (
	USER_AGENT,
	extract_content,
	rss_fulltext_html,
	total_chars,
	trim_chrome,
)

# 平均每段字數的合理區間。超出上限通常代表整塊 script/JSON 被當成段落收進來，
# 或這個來源根本不是文章（產品清單、導購頁）。
SANE_AVG_CHARS = (120, 700)


def analyse(name, rss_url):
	out = {"name": name or rss_url, "rss": rss_url}

	feed = feedparser.parse(rss_url)
	out["http"] = getattr(feed, "status", "?")
	out["entries"] = len(feed.entries)
	if not feed.entries:
		out["verdict"] = "不能用：RSS 沒有任何文章"
		return out

	entry = feed.entries[0]
	url = entry.get("link", "")
	out["sample"] = url

	rss_html = rss_fulltext_html(entry)
	rss_paras, _ = extract_content(BeautifulSoup(rss_html, "lxml"), url) if rss_html else ([], [])
	rss_paras, _ = trim_chrome(rss_paras)
	out["rss_paras"] = len(rss_paras)
	out["rss_chars"] = total_chars(rss_paras)

	try:
		resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
		out["page_http"] = resp.status_code
	except Exception as e:
		out["page_http"] = f"ERR {type(e).__name__}"
		out["verdict"] = "不能用：文章頁連不上"
		return out

	if resp.status_code != 200:
		if out["rss_chars"] >= 1200:
			out["verdict"] = f"可用（頁面 {resp.status_code} 被擋，但 RSS 有全文可頂）"
		else:
			out["verdict"] = f"不能用：頁面被擋（{resp.status_code}）且 RSS 沒有全文"
		return out

	page_paras, page_imgs = extract_content(BeautifulSoup(resp.text, "lxml"), url)
	page_paras, head = trim_chrome(page_paras)
	out["page_paras"] = len(page_paras)
	out["page_chars"] = total_chars(page_paras)
	out["images"] = len(page_imgs)

	best = max(out["page_chars"], out["rss_chars"])
	best_paras = out["page_paras"] if out["page_chars"] >= out["rss_chars"] else out["rss_paras"]
	out["avg"] = round(best / best_paras) if best_paras else 0

	problems = []
	if out["rss_chars"] and out["page_chars"] < out["rss_chars"] * 0.6:
		pct = out["page_chars"] / out["rss_chars"]
		problems.append(f"頁面疑似截斷（只有 RSS 的 {pct:.0%}，會自動改用 RSS）")
	if best_paras < 6 or best < 1200:
		problems.append(f"內容太短（{best_paras} 段 / {best} 字）")
	if out["avg"] > SANE_AVG_CHARS[1]:
		problems.append(f"平均每段 {out['avg']} 字，過長，可能抓到雜訊")
	elif out["avg"] and out["avg"] < SANE_AVG_CHARS[0]:
		problems.append(f"平均每段 {out['avg']} 字，過短，可能抓到導覽列")

	out["verdict"] = "可用" if not problems else "要注意：" + "；".join(problems)
	return out


def main():
	args = sys.argv[1:]
	if args:
		targets = [(None, u) for u in args]
	else:
		import json
		with open(Path(__file__).parent / "sources.json", encoding="utf-8") as f:
			targets = [(s["name"], s["rss"]) for s in json.load(f)]

	print(f"{'來源':22} {'篇':>4} {'RSS段':>6} {'RSS字':>7} {'頁段':>5} {'頁字':>7} {'字/段':>6}  判定")
	print("-" * 110)
	for name, rss in targets:
		try:
			r = analyse(name, rss)
		except Exception as e:
			print(f"{(name or rss)[:22]:22} 檢查時出錯：{e}")
			continue
		print(
			f"{r['name'][:22]:22} {r.get('entries', 0):>4} "
			f"{r.get('rss_paras', '-'):>6} {r.get('rss_chars', '-'):>7} "
			f"{r.get('page_paras', '-'):>5} {r.get('page_chars', '-'):>7} "
			f"{r.get('avg', '-'):>6}  {r['verdict']}"
		)
		if r.get("sample"):
			print(f"{'':22} 樣本：{r['sample'][:80]}")


if __name__ == "__main__":
	main()
