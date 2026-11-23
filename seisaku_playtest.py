# -*- coding: utf-8 -*-
"""
seisaku_playtest.py — 完成済みのPythonゲームを、夕凪が試遊して評価する単体スクリプト

seisaku_auto.py の試遊フェーズ（playtest_game）を、作り直しなしで
既存のゲームファイルに対してだけ実行する。判定はメモ帳に残る。

使い方:
  python seisaku_playtest.py seisaku\\games\\game-002.py
  python seisaku_playtest.py seisaku\\games\\game-002.py "じゃんけん大会"

置き場所: F:\\ai_agent\\server\\seisaku_playtest.py（seisaku_auto.py と同じフォルダ）
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from seisaku_auto import playtest_game


def main() -> int:
    if len(sys.argv) < 2:
        print("使い方: python seisaku_playtest.py <ゲームの.pyファイル> [題名]")
        return 0
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ファイルが無い: {path}")
        return 0
    title = sys.argv[2] if len(sys.argv) > 2 else path.stem
    decisions = ["決定：仕様はゲームの画面表示と一般的なルールに従う（単体試遊のため）"]
    print(f"[playtest] {title} を夕凪が試遊します")
    status, detail = playtest_game(path, title, decisions)
    print(f"[playtest] 結果: {status}")
    print(detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
