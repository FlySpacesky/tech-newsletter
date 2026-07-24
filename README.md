# 每日科技電子報自動化

每天台灣時間約 10:07，GitHub Actions 會抓取固定科技媒體的 RSS／Atom、產生響應式 HTML、發布到 GitHub Pages，確認新頁面可開啟後再廣播至 LINE 官方帳號。

公開網址：<https://flyspacesky.github.io/tech-newsletter/>

## 一次性設定

1. 建立公開儲存庫 `FlySpacesky/tech-newsletter`，不要勾選 README、`.gitignore` 或 License。
2. 將本專案全部檔案上傳到預設分支。
3. 到 **Settings → Pages → Build and deployment**，選擇 **Deploy from a branch**，分支選 `main`、資料夾選 `/docs`。
4. 到 **Settings → Secrets and variables → Actions → New repository secret**，新增：
   - Name：`LINE_CHANNEL_ACCESS_TOKEN`
   - Secret：LINE Developers Console 的 Channel access token（請勿貼在 Issue、程式碼或聊天中）。
5. 到 **Actions → Daily tech newsletter → Run workflow** 做一次測試。之後不必人工執行。

## 安全與可靠性

- LINE 訊息先呼叫 `/validate/broadcast` 驗證，再呼叫 `/broadcast`。
- 每天使用固定且不重複的 `X-Line-Retry-Key`；同一天重跑不會重複廣播。
- GitHub Pages 尚未更新時會取消 LINE 廣播，避免好友打開舊內容。
- 抓到少於 3 則有效新聞時不覆蓋舊電子報，也不廣播。
- Channel access token 僅存於 GitHub Actions Secret。

## 調整來源與時間

- 編輯 `sources.json` 可增刪來源或備援 Feed。
- 排程位於 `.github/workflows/daily-newsletter.yml`；目前使用 `Asia/Taipei` 的 `10:07`，避開整點的 Actions 高負載時段。
- GitHub 公開儲存庫若連續 60 天無活動，排程可能被停用；本流程正常每天提交新一期，因此通常不會觸發。

## 本機測試

```bash
python -m unittest discover -s tests -v
python scripts/generate_newsletter.py
```

`send_line.py` 只有在環境變數 `LINE_CHANNEL_ACCESS_TOKEN` 存在時才會送出訊息。
