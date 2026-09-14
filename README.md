# AI SWE Agent

自動盯著 [job-scraper](https://github.com/DennisHsu716/job-scraper) 的 bug/需求
issue、嘗試修好、開 PR 給人審核的維護機器人。目標寫死指向 job-scraper，不是
通用框架——先在一個真實案例上跑通，之後真的要接第二個專案再抽象化。

## 架構

```
排程輪詢 job-scraper issue (label: auto-fix)
  → 序列處理（concurrency group，一次只處理一個）
  → checkout job-scraper，claude CLI 執行修改 + 跑 tests/（Tier 1 迴歸測試）
  → 測試過 + 沒碰敏感檔案 → 開 PR，人工審核後才 merge（不自動 merge）
  → 測試沒過 / 沒有任何改動 / 碰到敏感檔案 → 標記 needs-human，附上嘗試紀錄
```

細節見 `.github/workflows/agent.yml`（trigger + orchestrator）跟
`scripts/run_agent.py`（core loop：呼叫 claude、跑測試把關、判斷成功/失敗）。

## 設定

這個 repo 需要兩把 secret（Settings → Secrets and variables → Actions）：

| Secret | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | claude CLI 執行修改用，去 [console.anthropic.com](https://console.anthropic.com) 申請 |
| `TARGET_REPO_PAT` | 一把有 job-scraper repo 權限的 GitHub Personal Access Token（需要 `repo` scope），因為預設的 `GITHUB_TOKEN` 只能操作這個 repo 自己，沒辦法跨 repo clone/push/開 PR 到 job-scraper |

job-scraper 那邊要手動建立三個 label（用來當作狀態機，這個 repo 不額外維護資料庫）：

- `auto-fix` — 貼這個標籤的 issue 才會被自動處理
- `swe-agent-in-progress` — agent 正在處理中（避免同一個 issue 被重複撈到）
- `swe-agent-needs-human` — agent 沒能自動修好，需要人工介入

## 護欄

- **範圍限制**：`run_agent.py` 只允許 claude 用 Read/Edit/Write/Grep/Glob 跟限定
  範圍的 Bash（pytest/python3/pip/git），不給任意指令，也不用
  `--dangerously-skip-permissions`（那個官方建議只給沒有網路的 sandbox，這裡
  runner 要連外，不適合整個放行）
- **敏感路徑**：改動碰到 `.github/workflows/`、`.env`、`config.py` 就算測試過了
  也一律轉 needs-human，不自動開 PR
- **安全閥**：單次執行花費上限 `--max-budget-usd 2`、牆鐘時間上限 30 分鐘，撞到
  任一個就當這輪失敗
- **PASS 定義**：不採信 agent 自己說「改完了」，一定重新獨立跑一次
  `pytest tests/` 才算數
- **序列處理**：`concurrency: group: swe-agent` 確保任何時候只有一個 run 在跑

## 現況

還沒有實際跑過完整流程（需要你先設定好上面兩把 secret、在 job-scraper 建好
三個 label）。建議先手動觸發一次（Actions 頁籤 → SWE Agent → Run workflow）
測試，看看有沒有需要調整的地方，再讓排程接手。
