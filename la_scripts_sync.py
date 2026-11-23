# -*- coding: utf-8 -*-
"""
la_scripts_sync.py — ムスビ → LA のスクリプト配布棚（den1234git/la-scripts）を
取りに行く係。server フォルダに置いて、デーモンの定型処理から1日1回呼ぶ。

やること:
  1. リポジトリのクローンが無ければ作る（同フォルダの la-scripts-clone\）。
     あれば git pull --ff-only で最新にする（Public なので認証なし）。
  2. 配布対象リスト（FILES）のファイルだけを、クローンから
     このスクリプトのあるフォルダ（server フォルダ）へコピーする。
     中身が変わったファイルだけ上書きし、結果を1行で出す。

やらないこと:
  - コピーしたファイルを実行しない。実行するのはデーモンの予定表の側。
  - リストに無いファイルはコピーしない。リポジトリ側で増えても、
    このリストに足すまで配られない。
  - リポジトリへ書き戻さない（一方向）。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_URL = "https://github.com/den1234git/la-scripts.git"
HERE = Path(__file__).resolve().parent
CLONE_DIR = HERE / "la-scripts-clone"

# 配布対象（このリポジトリの正本ファイル名と一致させる）
FILES = [
    "seisaku_auto.py",
    "seisaku_html.py",
    "seisaku_notice.py",
    "seisaku_fix.py",
    "seisaku_playtest.py",
    "la_deliver.py",
]


def run_git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if not CLONE_DIR.exists():
        res = run_git(["clone", REPO_URL, str(CLONE_DIR)])
        if res.returncode != 0:
            print(f"la-scripts 同期: 失敗（clone できませんでした）{res.stderr.strip()[:200]}")
            return 1
    else:
        res = run_git(["-C", str(CLONE_DIR), "pull", "--ff-only"])
        if res.returncode != 0:
            print(f"la-scripts 同期: 失敗（pull できませんでした）{res.stderr.strip()[:200]}")
            return 1

    updated: list[str] = []
    missing: list[str] = []
    for name in FILES:
        src = CLONE_DIR / name
        dst = HERE / name
        if not src.exists():
            missing.append(name)
            continue
        if dst.exists() and digest(src) == digest(dst):
            continue
        shutil.copy2(src, dst)
        updated.append(name)

    if missing:
        print(f"la-scripts 同期: リポジトリに無いファイルがあります: {', '.join(missing)}")
        return 1
    if updated:
        print(f"la-scripts 同期: 更新 {len(updated)} 件（{', '.join(updated)}）")
    else:
        print("la-scripts 同期: 新着なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
