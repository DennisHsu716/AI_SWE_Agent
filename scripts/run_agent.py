#!/usr/bin/env python3
"""
run_agent.py — SWE Agent Core Loop，對著已經 checkout 好的 target repo 跑。

呼叫端（agent.yml）負責：挑 issue、checkout target repo、跑完之後根據這支
腳本的 exit code 決定要開 PR 還是標記 needs-human。這支腳本只做兩件事：
呼叫 Aider（接 Gemini）修東西、用 pytest 當作唯一可信的把關標準（不採信
agent 自己說「改完了」，一定重新跑一次測試才算數）。

用 Aider 而不是 claude CLI：Aider 本身不執行任意 shell 指令（沒有 Bash
tool 這種東西），只做「讀檔案→提出修改→寫回去」，可以指定 --test-cmd 讓
它自己邊改邊跑測試。代價是沒有花費上限（--max-budget-usd 那種機制不存
在），牆鐘 timeout 變成唯一的安全閥；而且它自己重試幾次沒有文件記載，所以
最後這支腳本還是要獨立重跑一次 pytest 才算數，不能只看 Aider 自己回報的
結果。

Exit code：
  0 = 測試通過，而且有實際改動 → 呼叫端開 PR
  1 = 沒有任何改動（agent 判斷不需要改，或想不出怎麼改）→ 呼叫端不開 PR，
      直接標記需要人看
  2 = 有改動但測試沒過 → 呼叫端標記需要人看，附上這次的嘗試紀錄
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# 可以用環境變數覆寫，不用改程式碼——job-scraper 自己的 config.py 也是
# 這種「模型字串集中一個地方、方便換」的做法。Gemini 免費版額度很緊
# （job-scraper 的 config.py 註解寫生成模型大概每天只有 20~40 次），Aider
# 一次 coding session 打的 API 次數遠高於 job-scraper 原本「一個職缺一次」
# 的用量，額度可能撐不住，必要時去 Google AI Studio 開付費層級。
DEFAULT_AIDER_MODEL = "gemini/gemini-2.5-pro"

# 沒有花費上限機制，牆鐘時間上限是唯一的安全閥，撞到就視為這一輪失敗，
# 轉 needs-human，不無限期燒下去。
WALL_CLOCK_TIMEOUT_SECONDS = 1800

# 這些路徑碰到就一律轉 needs-human，就算測試過了也一樣——CI 設定、密鑰處理、
# 已提交的快取檔案，改壞了影響範圍比一般 bug fix 大，值得多一道人工確認。
SENSITIVE_PATH_PREFIXES = (
    ".github/workflows/",
    ".env",
)
SENSITIVE_FILES = {
    "config.py",  # 密鑰讀取邏輯在這裡，改動要額外小心
}


def build_prompt(issue_number: int, issue_title: str, issue_body: str) -> str:
    return f"""你在修一個叫 job-scraper 的 Python 專案裡的 bug/需求，來源是 GitHub issue #{issue_number}。

標題：{issue_title}

內容：
{issue_body}

規則（務必遵守，不是建議）：
1. 改動範圍盡量小，只處理這個 issue 描述的問題，不要順手重構、不要動不相關的檔案。
2. 不要修改 .github/workflows/、.env、config.py 裡跟密鑰讀取相關的部分，除非 issue 本身明確要求改這些。
3. 如果這次修改牽涉 scorer.py 裡的判斷邏輯（is_intern / is_us_based / should_include / grade_fit 之類的函式），
   必須在 tests/test_scorer_regression.py 補一條對應的迴歸案例，格式跟現有案例一致（案例上方要有註解說明
   這對應哪個真實情境，不是憑空編的輸入）。
4. 改完之後自己跑 `pytest tests/ -v`，反覆修到全部通過為止。如果卡住想不出怎麼修，不要硬套一個
   會通過測試但邏輯不對的解法，直接停下來，最後的回覆裡清楚說明卡在哪裡、已經試過什麼。
5. 不要 commit、不要 push、不要開 PR——你只負責把工作目錄改好，後面的流程由呼叫端處理。

完成後用一段簡短的中文摘要說明你做了什麼改動、為什麼，還有測試結果。"""


def run_aider(prompt: str, cwd: Path) -> str:
    """呼叫 Aider 修改 cwd 底下的檔案，回傳它的文字輸出（不代表成功與否，
    成功與否交給呼叫端用 git diff + pytest 獨立驗證）。"""
    model = os.environ.get("AIDER_MODEL", DEFAULT_AIDER_MODEL)

    # 一次性 --message 模式下，Aider 能不能自己發現該改哪個檔案沒有明確
    # 文件保證，保險起見明確把 repo 裡的 .py 檔案都列給它，不要賭它會自動
    # 找到——job-scraper 檔案不多，全列出來成本很低。
    py_files = sorted(str(p.relative_to(cwd)) for p in cwd.glob("*.py"))
    py_files += sorted(str(p.relative_to(cwd)) for p in (cwd / "tests").glob("*.py"))

    cmd = [
        "aider",
        "--yes-always",
        "--no-auto-commits",
        "--model", model,
        "--test-cmd", "pytest tests/ -v",
        "--auto-test",
        "--message", prompt,
        *py_files,
    ]
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=WALL_CLOCK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        print(f"::warning::aider 執行超過 {WALL_CLOCK_TIMEOUT_SECONDS} 秒，中止", file=sys.stderr)
        partial = (e.stdout or b"").decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return partial[-4000:]

    if result.returncode != 0:
        print("::warning::aider 非正常結束", file=sys.stderr)
        print(result.stderr[-4000:], file=sys.stderr)

    return (result.stdout + result.stderr)[-4000:]


def get_changed_files(cwd: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=cwd,
        capture_output=True, text=True, check=True,
    )
    return [line[3:].strip() for line in result.stdout.splitlines() if line.strip()]


def touches_sensitive_paths(changed_files: list[str]) -> bool:
    for f in changed_files:
        if f in SENSITIVE_FILES:
            return True
        if any(f.startswith(prefix) for prefix in SENSITIVE_PATH_PREFIXES):
            return True
    return False


def run_tests(cwd: Path) -> tuple[bool, str]:
    result = subprocess.run(
        ["pytest", "tests/", "-v"], cwd=cwd,
        capture_output=True, text=True, timeout=300,
    )
    output = (result.stdout + result.stderr)[-4000:]
    return result.returncode == 0, output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--issue-title", required=True)
    parser.add_argument("--issue-body-file", required=True)
    parser.add_argument("--repo-dir", default=".")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir).resolve()
    issue_body = Path(args.issue_body_file).read_text(encoding="utf-8")

    prompt = build_prompt(args.issue_number, args.issue_title, issue_body)
    summary = run_aider(prompt, repo_dir) or "(aider 沒有回傳輸出)"

    changed_files = get_changed_files(repo_dir)

    if not changed_files:
        Path("agent_summary.md").write_text(
            f"Agent 判斷沒有需要修改的地方，或執行未完成。\n\n{summary}",
            encoding="utf-8",
        )
        print("no changes made")
        return 1

    if touches_sensitive_paths(changed_files):
        Path("agent_summary.md").write_text(
            f"改動碰到敏感路徑（CI 設定/密鑰相關/config.py），需要人工確認，不自動開 PR。\n\n"
            f"改動的檔案：{', '.join(changed_files)}\n\n{summary}",
            encoding="utf-8",
        )
        print("touched sensitive paths, needs human")
        return 2

    tests_passed, test_output = run_tests(repo_dir)
    if not tests_passed:
        Path("agent_summary.md").write_text(
            f"測試沒有通過。\n\n改動的檔案：{', '.join(changed_files)}\n\n"
            f"Agent 摘要：\n{summary}\n\n測試輸出：\n```\n{test_output}\n```",
            encoding="utf-8",
        )
        print("tests failed")
        return 2

    Path("agent_summary.md").write_text(
        f"測試通過。\n\n改動的檔案：{', '.join(changed_files)}\n\n{summary}",
        encoding="utf-8",
    )
    print("success")
    return 0


if __name__ == "__main__":
    sys.exit(main())
