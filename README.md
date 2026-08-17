# 每兩日科技電子報自動化

每天台灣時間約 10:07，GitHub Actions 會彙整「今天＋昨天」兩個日期的科技新聞，產生單一響應式 HTML、發布到 GitHub Pages，確認新頁面可開啟後再廣播至 LINE 官方帳號。

最新一期：<https://flyspacesky.github.io/tech-newsletter/>

每日產生的版本另存於 `docs/archive/YYYY-MM-DD.html`。LINE 每一期都使用該日期的固定封存網址，例如 `https://flyspacesky.github.io/tech-newsletter/archive/2026-08-17.html`，所以日後重新開啟舊訊息時仍會看到當時那一期，不會被最新首頁取代。

## 8 個固定來源

1. Flipboard 科技專區
2. 科技島 TechNice
3. INSIDE 硬塞的網路趨勢觀察
4. TechNews 科技新報
5. 數位時代 BNext（最新文章）
6. TechOrange 科技報橘（最新文章）
7. Meet 創業小聚（最新文章）
8. Cool3c

抓取器會讀取各站 Feed 與可用的最新列表。支援分頁的網站會逐頁往後讀取，直到該頁文章已早於本期日期範圍，或頁面不再出現新文章才停止，不再固定只讀 3 頁。系統只保留台灣時區今天及昨天、能確認發布日期的文章，最後依正規化網址去重。另保留最近 7 天的文章快取：像 Meet 或 Flipboard 的 Feed 只顯示最新批次時，下一期仍能從快取補回昨天文章。個別來源失效不會中斷其他來源；若全部來源都沒有合格文章，則保留前一期且不廣播。

Flipboard 使用其科技專區 RSS，直接取得精確發布時間、摘要與圖片；TechNews 的 RSS 文章如果缺少圖片，系統會優先從最新新聞列表補入該文章縮圖，也會重用 Flipboard 收到的同篇文章圖片。Feed 或列表暫時出現 429、5xx、逾時等錯誤時會自動重試，仍無法取得時再由最近 7 天快取補回兩日範圍內的文章。

## 電子報版面

- 頁首：標題、阿拉伯數字日期範圍、8 個來源、文章總數、兩日自動翻頁狀態。
- 本期來源：列出全部 8 個網站。
- 正文：依來源網站分成 8 區，每區顯示文章數。
- 文章卡片：桌面版左圖右文；圖片固定為橫式 `16:10`，不會再隨摘要高度被拉成細長圖。
- iPad／平板：圖片欄縮為 220px，維持橫式比例與完整文字空間。
- 手機：一般手機採 140px 的 `4:3` 小圖＋文字，窄螢幕改為上圖下文的 `16:9` 排列。
- 時間：每篇同時顯示「3 小時前」等相對時間，以及 `2026.07.24 14:31` 的台灣時間；開啟頁面後會依當下時間立即重算，並每分鐘更新。

## 一次性設定

1. GitHub Pages：**Settings → Pages → Deploy from a branch → `main` → `/docs`**。
2. GitHub Actions Secret：建立 `LINE_CHANNEL_ACCESS_TOKEN`。
3. Actions：執行 **Daily tech newsletter → Run workflow** 測試。

## 安全與可靠性

- LINE 訊息先呼叫 `/validate/broadcast` 驗證，再呼叫 `/broadcast`。
- 每天使用固定的 `X-Line-Retry-Key`，同一天重跑不會重複廣播。
- GitHub Pages 尚未更新時會取消 LINE 廣播。
- 發送前會確認當期日期固定封存頁已發布，LINE outbox 若仍指向會變動的首頁則拒絕廣播。
- Channel access token 僅存於 GitHub Actions Secret。

## 本機測試

```bash
python -m unittest discover -s tests -v
python scripts/generate_newsletter.py
```

`send_line.py` 只有在環境變數 `LINE_CHANNEL_ACCESS_TOKEN` 存在時才會送出訊息。
