# -*- coding: utf-8 -*-
"""
seisaku_notice.py — 「気づく」ための答え合わせスクリプト

判定を聞いても小さいモデルは気づかない。代わりに手順で気づかせる:
  1. 期待（夕凪）: 遊ぶ前に、ゲームの説明と最初の画面から
     「こうなるはず」を箇条書きで書く
  2. 試遊（夕凪）: いつもの試遊で10回遊び、記録を残す
  3. 書き出し（夕凪）: 記録から、起きたことを解釈なしで表に写す
  4. 答え合わせ（巌）: 期待と書き出しを1つずつ照らし、食い違いを挙げる

結果は全部メモ帳に残る。直すかどうかは、この結果を見てから決める。

使い方:
  python seisaku_notice.py seisaku\\games\\game-003.py "コイントス"
  python seisaku_notice.py seisaku\\games\\game-003.py "コイントス" "表か裏かを予想して当てる。持ち点10点、1回1点賭けて当たれば2点戻る"

置き場所: F:\\ai_agent\\server\\seisaku_notice.py（seisaku_auto.py と同じフォルダ）
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from seisaku_auto import ask, playtest_game, post_to_memo, log

# 答え合わせ（照合）をやる人。全員が別々に（互いの答えを見ずに）照合する。
CHECKERS = ["夕凪", "星月", "巌"]


def first_screen(path: Path) -> str:
    """ゲームを起動して、最初の画面（説明・入力待ちの文）だけを拾う。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(path.resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", cwd=str(path.resolve().parent), env=env)
    except Exception:
        return ""
    q: "queue.Queue[str]" = queue.Queue()

    def reader():
        try:
            while True:
                ch = proc.stdout.read(1)
                if not ch:
                    break
                q.put(ch)
        except Exception:
            pass

    threading.Thread(target=reader, daemon=True).start()
    text = ""
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            text += q.get(timeout=0.2)
        except queue.Empty:
            if text:
                break
    try:
        proc.kill()
    except Exception:
        pass
    return text


def main() -> int:
    if len(sys.argv) < 2:
        print("使い方: python seisaku_notice.py <ゲームの.pyファイル> [題名] [説明文]")
        return 0
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ファイルが無い: {path}")
        return 0
    title = sys.argv[2] if len(sys.argv) > 2 else path.stem
    desc = sys.argv[3] if len(sys.argv) > 3 else title
    decisions = [f"決定：仕様はゲームの画面表示と一般的なルールに従う（{desc}）"]

    # 1. 期待（夕凪）: 遊ぶ前に「こうなるはず」を書く
    screen = first_screen(path)
    try:
        expect = ask("夕凪",
                     f"あなたは村の制作チームの一員、夕凪です。これから星月のゲーム『{title}』を試しに遊びます。\n"
                     f"ゲームの説明：{desc}\n"
                     f"ゲームの最初の画面：\n{screen}\n\n"
                     "今回の課題：遊ぶ前に、このゲームが「こうなるはず」という期待を3〜5個、"
                     "1行ずつ「〜はず」で書いてください（例：「当たれば、賭けた点だけ増えるはず」）。"
                     "まだ遊んでいないので、結果は書かないこと。")
    except Exception as e:
        log({"phase": "notice-expect", "name": "夕凪", "error": f"{type(e).__name__}: {e}"})
        print("[notice] 夕凪の呼び出し（期待）に失敗")
        return 0
    post_to_memo("夕凪", f"「{title}」の答え合わせ 1/3 期待（遊ぶ前）\n{expect}")
    print("[notice] 1/3 期待を書いた")

    # 2. 試遊（夕凪）: いつもの試遊。判定は使わず、記録だけを使う
    playtest_game(path, title, decisions)
    play_path = path.with_name(path.stem + ".play.txt")
    transcript = play_path.read_text(encoding="utf-8", errors="replace") if play_path.exists() else ""
    if not transcript.strip():
        print("[notice] 試遊の記録が取れなかった")
        return 0
    print("[notice] 2/3 試遊の記録ができた")

    # 3. 書き出し（夕凪）: 記録を解釈なしで写す
    try:
        transcript_text = ask("夕凪",
                              f"あなたが遊んだゲーム『{title}』の記録です。\n\n{transcript[-6000:]}\n\n"
                              "今回の課題：記録に起きたことを、1回分ずつ、そのまま表に書き出してください。"
                              "入力したもの、画面に出た結果、点数の変化があればそれも書いてください。"
                              "感想や解釈は書かないこと。起きたことだけを写してください。")
    except Exception as e:
        log({"phase": "notice-copy", "name": "夕凪", "error": f"{type(e).__name__}: {e}"})
        print("[notice] 夕凪の呼び出し（書き出し）に失敗")
        return 0
    post_to_memo("夕凪", f"「{title}」の答え合わせ 2/3 書き出し（記録の写し）\n{transcript_text}")
    print("[notice] 3/3 書き出しができた")

    # 4. 答え合わせ（全員）: 期待と書き出しを、各自が別々に照らす
    any_found = False
    for name in CHECKERS:
        try:
            check = ask(name,
                        f"あなたは村の制作チームの一員、{name}です。ゲーム『{title}』について、"
                        "夕凪が遊ぶ前に書いた「期待」と、実際に起きたことの「書き出し」を照合してください。\n\n"
                        f"=== 期待 ===\n{expect}\n\n=== 書き出し ===\n{transcript_text}\n\n"
                        "今回の課題：期待を1つずつ取り上げ、書き出しの内容と「合う」か「合わない」かを書いてください。"
                        "合わない場合は、書き出しのどの行と食い違うかを書いてください。\n"
                        "最後の行に、食い違いがあれば「答え合わせ：食い違いあり」、無ければ「答え合わせ：食い違いなし」と書いてください。",
                        num_predict=900)
        except Exception as e:
            log({"phase": "notice-check", "name": name, "error": f"{type(e).__name__}: {e}"})
            print(f"[notice] {name}の呼び出しに失敗（飛ばす）")
            continue
        post_to_memo(name, f"「{title}」の答え合わせ 3/3 照合結果（{name}）\n{check}")
        norm = check.replace("：", ":").replace(" ", "").replace("　", "")
        body = [ln for ln in norm.splitlines() if "答え合わせ" not in ln]
        # 「合わない」と書かない言い回し（合致していません／合いません／不一致）も印にする
        mismatch_words = ("合わない", "食い違", "合致していません", "合致しません",
                          "合っていない", "合いません", "不一致")
        mismatch_marks = sum(1 for ln in body
                             if any(w in ln for w in mismatch_words))
        found = mismatch_marks > 0 or "食い違いあり" in norm
        any_found = any_found or found
        print(f"[notice] 照合 {name}: {'食い違いあり' if found else '食い違いなし'}"
              f"（合わない印 {mismatch_marks}件）")
    print(f"[notice] 答え合わせ（全員）: {'食い違いあり' if any_found else '食い違いなし'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
