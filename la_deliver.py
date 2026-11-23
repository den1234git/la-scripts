"""
LA→ムスビ 配達スクリプト。

固定リストの産物だけを集めて、配達専用リポジトリ（den1234git/la-reports）へ
commit / push する。git の操作は全部このスクリプト（プログラム側）の仕事で、
配達係のモデルは「実行して、成否の行を確認して、ログに1行残す」だけ。

送るもの（固定リスト。これ以外は送らない）:
  - 感想文リレーの結果      kansou_relay/<NNN>/ の *.md と measurement.jsonl
  - 制作チームのメモと成果物 seisaku/seisaku_memo.md と seisaku/games/*.py
  - 状態報告                kyozai_inbox.txt（教材の到着記録）
  - 改善案（提案書）        docs/改善案_*.md → reports/proposals/
  - read_sheet（日記・教訓） READ_SHEET_PATH を設定した時だけ
  - 広場の記録              PLAZA_DIR を設定した時だけ

絶対に送らないもの:
  - 自由メモ（F:\\kagura\\free_memo には一切触らない。上のリストに経路自体がない）

使い方: python la_deliver.py
  - 送るものが無ければ「配達なし」で終わる。
  - 失敗しても例外で落ちず、理由を表示して rc=0 で終わる（呼び出し側を止めない）。
  - 実行のたびに la_deliver.log へ1行残す。
"""
import os
import sys
import json
import hashlib
import shutil
import subprocess
import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO_URL = "https://github.com/den1234git/la-reports.git"
_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.join(_DIR, "la_reports_repo")
STATE_PATH = os.path.join(_DIR, "la_deliver_state.json")
LOG_PATH = os.path.join(_DIR, "la_deliver.log")
GIT_TIMEOUT = 120

# --- 送り元の設定（パスが分からないものは空のまま。空ならその分類は飛ばす） ---
KANSOU_DIR = os.path.join(_DIR, "kansou_relay")       # 感想文リレーの出力先
SEISAKU_DIR = os.path.join(_DIR, "seisaku")           # 制作チームのフォルダ
KYOZAI_INBOX = os.path.join(_DIR, "kyozai_inbox.txt")  # 教材の到着記録
READ_SHEET_PATH = ""   # 例: read_sheet のファイル or フォルダのパス
PLAZA_DIR = ""         # 例: 広場の記録フォルダのパス


def now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def log_line(text: str) -> None:
    line = f"{now_str()} {text}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def git(args, cwd=REPO_DIR) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
    )


def sha1_of(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"delivered": {}}


def save_state(state: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def gather() -> list:
    """(送り元パス, リポジトリ内の相対パス) の一覧を固定リストから作る。"""
    items = []
    # 感想文リレー
    if os.path.isdir(KANSOU_DIR):
        for nnn in sorted(os.listdir(KANSOU_DIR)):
            sub = os.path.join(KANSOU_DIR, nnn)
            if not os.path.isdir(sub):
                continue
            for name in sorted(os.listdir(sub)):
                if name.endswith(".md") or name == "measurement.jsonl":
                    items.append((os.path.join(sub, name),
                                  f"reports/kansou/{nnn}/{name}"))
    # 制作チーム
    memo = os.path.join(SEISAKU_DIR, "seisaku_memo.md")
    if os.path.isfile(memo):
        items.append((memo, "reports/seisaku/seisaku_memo.md"))
    games = os.path.join(SEISAKU_DIR, "games")
    if os.path.isdir(games):
        for name in sorted(os.listdir(games)):
            if name.endswith(".py") or name.endswith(".play.txt"):
                items.append((os.path.join(games, name),
                              f"reports/seisaku/games/{name}"))
    # 制作チーム（HTMLゲーム）
    html_games = os.path.join(SEISAKU_DIR, "html_games")
    if os.path.isdir(html_games):
        for name in sorted(os.listdir(html_games)):
            if name.endswith(".html"):
                items.append((os.path.join(html_games, name),
                              f"reports/seisaku/html_games/{name}"))
    # 状態報告（教材の到着記録）
    if os.path.isfile(KYOZAI_INBOX):
        items.append((KYOZAI_INBOX, "reports/status/kyozai_inbox.txt"))
    # 改善案（クロードからムスビへの提案書。改善案_ で始まる .md だけ）
    docs_dir = os.path.join(_DIR, "docs")
    if os.path.isdir(docs_dir):
        for name in sorted(os.listdir(docs_dir)):
            if name.startswith("改善案_") and name.endswith(".md"):
                items.append((os.path.join(docs_dir, name),
                              f"reports/proposals/{name}"))
    # read_sheet（設定時のみ。ファイルでもフォルダでも可）
    if READ_SHEET_PATH and os.path.isfile(READ_SHEET_PATH):
        items.append((READ_SHEET_PATH,
                      "reports/read_sheet/" + os.path.basename(READ_SHEET_PATH)))
    elif READ_SHEET_PATH and os.path.isdir(READ_SHEET_PATH):
        for name in sorted(os.listdir(READ_SHEET_PATH)):
            p = os.path.join(READ_SHEET_PATH, name)
            if os.path.isfile(p):
                items.append((p, f"reports/read_sheet/{name}"))
    # 広場の記録（設定時のみ）
    if PLAZA_DIR and os.path.isdir(PLAZA_DIR):
        for name in sorted(os.listdir(PLAZA_DIR)):
            p = os.path.join(PLAZA_DIR, name)
            if os.path.isfile(p):
                items.append((p, f"reports/plaza/{name}"))
    return items


def ensure_repo() -> bool:
    """クローンを用意し、最新へ寄せる。失敗したら False。"""
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        r = subprocess.run(
            ["git", "clone", REPO_URL, REPO_DIR], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
        )
        if r.returncode != 0:
            log_line(f"配達失敗：clone できませんでした ({r.stderr.strip()[:200]})")
            return False
    else:
        r = git(["pull", "--ff-only"])
        if r.returncode != 0:
            log_line(f"配達失敗：pull できませんでした ({r.stderr.strip()[:200]})")
            return False
    # コミット用の名前が無ければ、このリポジトリだけに仮の名前を付ける
    if git(["config", "user.email"]).returncode != 0:
        git(["config", "user.email", "la-delivery@localhost"])
        git(["config", "user.name", "LA配達係"])
    return True


def main() -> int:
    if not ensure_repo():
        return 0
    state = load_state()
    delivered = state.setdefault("delivered", {})

    copied = []
    for src, rel in gather():
        try:
            digest = sha1_of(src)
        except Exception:
            continue
        if delivered.get(rel) == digest:
            continue
        dst = os.path.join(REPO_DIR, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        delivered[rel] = digest
        copied.append(rel)

    if not copied:
        log_line("配達なし（新しいものはありません）")
        save_state(state)
        return 0

    git(["add", "-A"])
    r = git(["commit", "-m", f"配達 {now_str()}（{len(copied)}件）"])
    if r.returncode != 0:
        log_line(f"配達失敗：commit できませんでした ({r.stderr.strip()[:200]})")
        return 0
    r = git(["push"])
    if r.returncode != 0:
        # push だけ失敗したら、状態は保存しない（次回また送る）
        log_line(f"配達失敗：push できませんでした ({r.stderr.strip()[:200]})")
        return 0

    save_state(state)
    log_line(f"配達成功：{len(copied)}件（" + "、".join(copied[:5])
             + ("…" if len(copied) > 5 else "") + "）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
