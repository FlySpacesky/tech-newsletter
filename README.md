# 每兩日科技電子報自動化

每天台灣時間約 10:07，GitHub Actions 會彙整「今天＋昨天」兩個日期的科技新聞，產生單一響應式 HTML、發布到 GitHub Pages，確認新頁面可開啟後再廣播至 LINE 官方帳號。

公開網址：<https://flyspacesky.github.io/tech-newsletter/>

## 8 個固定來源

1. Flipboard 科技專區
2. 科技島 TechNice
3. INSIDE 硬塞的網路趨勢觀察
4. TechNews 科技新報
5. 數位時代 BNext（最新文章）
6. TechOrange 科技報橘（最新文章）
7. Meet 創業小聚（最新文章）
8. Cool3c

抓取器會讀取各站 Feed 與可用的最新列表；支援傳統分頁的網站最多讀取第 1–3 頁，沒有傳統分頁的來源則讀取其最新列表。系統只保留台灣時區今天及昨天、能確認發布日期的文章，最後依正規化網址去重。個別來源失效不會中斷其他來源；若全部來源都沒有合格文章，則保留前一期且不廣播。

## 電子報版面

- 頁首：標題、阿拉伯數字日期範圍、8 個來源、文章總數、最多 3 頁抓取深度。
- 本期來源：列出全部 8 個網站。
- 正文：依來源網站分成 8 區，每區顯示文章數。
- 文章卡片：桌面版左圖右文；右側包含日期時間、標題、摘要與原文連結。
- 行動版：窄螢幕自動改為小圖＋文字或上下排列。

## 一次性設定

1. GitHub Pages：**Settings → Pages → Deploy from a branch → `main` → `/docs`**。
2. GitHub Actions Secret：建立 `LINE_CHANNEL_ACCESS_TOKEN`。
3. Actions：執行 **Daily tech newsletter → Run workflow** 測試。

## 安全與可靠性

- LINE 訊息先呼叫 `/validate/broadcast` 驗證，再呼叫 `/broadcast`。
- 每天使用固定的 `X-Line-Retry-Key`，同一天重跑不會重複廣播。
- GitHub Pages 尚未更新時會取消 LINE 廣播。
- Channel access token 僅存於 GitHub Actions Secret。

## 本機測試

```bash
python -m unittest discover -s tests -v
python scripts/generate_newsletter.py
```

`send_line.py` 只有在環境變數 `LINE_CHANNEL_ACCESS_TOKEN` 存在時才會送出訊息。
