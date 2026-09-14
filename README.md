# AI SWE Agent

自動盯著 [job-scraper](https://github.com/DennisHsu716/job-scraper) 的 bug/需求
issue、嘗試修好、開 PR 給人審核的維護機器人。目標寫死指向 job-scraper，不是
通用框架——先在一個真實案例上跑通，之後真的要接第二個專案再抽象化。

## 架構

```
排程輪詢 job-scraper issue (label: auto-fix)
  → 序列處理（concurrency group，一次只處理一個）
  → checkout job-scraper，Aider（接 Gemini）執行修改 + 跑 tests/（Tier 1 迴歸測試）
  → 測試過 + 沒碰敏感檔案 → 開 PR，人工審核後才 merge（不自動 merge）
  → 測試沒過 / 沒有任何改動 / 碰到敏感檔案 → 標記 needs-human，附上嘗試紀錄
```

細節見 `.github/workflows/agent.yml`（trigger + orchestrator）跟
`scripts/run_agent.py`（core loop：呼叫 Aider、跑測試把關、判斷成功/失敗）。

Core loop 原本用 claude CLI，改用 Aider + Gemini 是因為不想只綁 Anthropic
一家——這兩者對 trigger/orchestrator/PR 審核這幾層完全透明，之後想換回
claude CLI 或換別家，只要改 `run_agent.py` 裡呼叫 LLM 的那個函式，其他都
不用動。

## 設定

這個 repo 需要兩把 secret（Settings → Secrets and variables → Actions）：

| Secret | 用途 |
|---|---|
| `GEMINI_API_KEY` | Aider 執行修改用，去 [Google AI Studio](https://aistudio.google.com/apikey) 申請。**注意**：免費額度可能不夠——Aider 一次 coding session（讀 repo map + 多輪修改 + auto-test 重試）打的 API 次數，遠高於 job-scraper 自己「一個職缺打一次」的用量，job-scraper 的 `config.py` 註解裡就寫過免費版生成模型大概每天只有 20~40 次額度。撞到額度不夠時考慮開付費層級 |
| `TARGET_REPO_PAT` | 一把有 job-scraper repo 權限的 GitHub Personal Access Token（需要 `repo` scope），因為預設的 `GITHUB_TOKEN` 只能操作這個 repo 自己，沒辦法跨 repo clone/push/開 PR 到 job-scraper |

job-scraper 那邊要手動建立三個 label（用來當作狀態機，這個 repo 不額外維護資料庫）：

- `auto-fix` — 貼這個標籤的 issue 才會被自動處理
- `swe-agent-in-progress` — agent 正在處理中（避免同一個 issue 被重複撈到）
- `swe-agent-needs-human` — agent 沒能自動修好，需要人工介入

## 護欄

- **範圍限制**：Aider 本身不執行任意 shell 指令（沒有 Bash tool 這種東西），
  只做「讀檔案→提出修改→寫回去」，`--test-cmd "pytest tests/ -v"` 是它唯一
  會執行的外部指令
- **敏感路徑**：改動碰到 `.github/workflows/`、`.env`、`config.py` 就算測試過了
  也一律轉 needs-human，不自動開 PR
- **安全閥**：牆鐘時間上限 30 分鐘（Aider 沒有花費上限機制，這是唯一的安全
  閥），撞到就當這輪失敗
- **PASS 定義**：不採信 agent 自己說「改完了」（Aider 的 `--auto-test` 重試
  次數沒有文件保證上限），一定重新獨立跑一次 `pytest tests/` 才算數
- **序列處理**：`concurrency: group: swe-agent` 確保任何時候只有一個 run 在跑

## 現況

還沒有實際跑過完整流程（需要你先設定好上面兩把 secret、在 job-scraper 建好
三個 label）。Aider 的 CLI 用法是查官方文件確認的（不是憑印象寫的），但
「一次性 `--message` 模式下能不能自己找到該改哪個檔案」這點文件沒有明確保證，
所以 `run_agent.py` 保守地把 repo 裡的 `.py` 檔案都明確列給它，繞開這個不確定性。

建議先手動觸發一次（Actions 頁籤 → SWE Agent → Run workflow）測試，看看有
沒有需要調整的地方，再讓排程接手。
