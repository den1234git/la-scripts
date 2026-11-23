# -*- coding: utf-8 -*-
"""
seisaku_fix.py — 試遊でおかしい点が出たゲームを、制作チーム自身が直す単体スクリプト

流れ:
  1. 試遊の記録（<ゲーム名>.play.txt）とコードを読み込む
     （記録が無ければ、先に試遊して記録を作る）
  2. 巌が照合表を作って食い違いを探す（書き手でない目で検査する）
     食い違いが無ければ、ここで終わる
  3. 星月が、巌の照合結果をもとに直したコードを書く
     （夕凪の判定文は鵜呑みにさせない。証拠は記録のほう）
  4. 元のコードは <ゲーム名>.before-fix.py に残し、直したコードで上書きする
  5. 機械テスト → もう一度試遊して、直ったか確かめる
  6. 結果をメモ帳に記録する

直しは1回だけ。まだおかしければ、記録を残して止まる（次に人間が判断する）。

使い方:
  python seisaku_fix.py seisaku\\games\\game-002.py "じゃんけん大会"

置き場所: F:\\ai_agent\\server\\seisaku_fix.py（seisaku_auto.py と同じフォルダ）
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from seisaku_auto import ask, extract_code, test_game, playtest_game, post_to_memo, log


def main() -> int:
    if len(sys.argv) < 2:
        print("使い方: python seisaku_fix.py <ゲームの.pyファイル> [題名]")
        return 0
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ファイルが無い: {path}")
        return 0
    title = sys.argv[2] if len(sys.argv) > 2 else path.stem
    code = path.read_text(encoding="utf-8", errors="replace")
    play_path = path.with_name(path.stem + ".play.txt")
    decisions = ["決定：仕様はゲームの画面表示と一般的なルールに従う（単体試遊のため）"]

    if not play_path.exists():
        print("[fix] 試遊の記録が無いので、先に試遊します")
        st, _ = playtest_game(path, title, decisions)
        if st != "problem":
            print(f"[fix] 試遊の結果は {st}。直す対象が無いので終わります")
            return 0
    transcript = play_path.read_text(encoding="utf-8", errors="replace") if play_path.exists() else ""

    # 巌：照合（書き手でない目で、表にして食い違いを探す）
    try:
        gan_check = ask("巌",
                        f"あなたは村の制作チームの一員、巌です。星月が書いたゲーム『{title}』の"
                        "判定（勝敗・スコアなど）が正しいか、コードと試遊の記録で照合してください。\n\n"
                        f"=== 試遊の記録 ===\n{transcript[-3000:]}\n\n"
                        f"=== コード ===\n{code}\n\n"
                        "今回の課題：判定に関わる全ての組み合わせについて、"
                        "「コードが出す結果」と「ルール上の正しい結果」を並べた照合表を書いてください。"
                        "入力が数字のときは名前に読み替えること（このゲームなら 1=グー、2=チョキ、3=パー）。"
                        "試遊の記録に出た組み合わせは、記録の結果とも照らしてください。\n"
                        "最後の行に、食い違いがあれば「照合：食い違いあり」、無ければ「照合：食い違いなし」と書いてください。",
                        num_predict=900)
    except Exception as e:
        log({"phase": "fix-check", "name": "巌", "error": f"{type(e).__name__}: {e}"})
        print("[fix] 巌の呼び出しに失敗")
        return 0
    post_to_memo("巌", gan_check)
    norm = gan_check.replace("：", ":").replace(" ", "").replace("　", "")
    last_lines = [ln for ln in norm.splitlines() if ln.strip()]
    gan_found = ("食い違いあり" in (last_lines[-1] if last_lines else "")) or \
                ("食い違いあり" in norm and "食い違いなし" not in (last_lines[-1] if last_lines else ""))
    print(f"[fix] 巌の照合: {'食い違いあり' if gan_found else '食い違いなし'}")
    if not gan_found:
        post_to_memo("ムスビ（自動）", f"「{title}」の直し回：巌の照合でも食い違いなし（変更なし）")
        print("[fix] 巌も食い違いなしと判断。変更なしで終わります")
        return 0

    # 星月：巌の照合をもとに直す
    try:
        fixed_text = ask("星月",
                         f"あなたは村の制作チームの一員、星月です。あなたが書いたゲーム『{title}』を、"
                         "巌が照合して食い違いを見つけました。\n\n"
                         f"=== 巌の照合 ===\n{gan_check}\n\n"
                         f"=== 試遊の記録 ===\n{transcript[-3000:]}\n\n"
                         f"=== あなたのコード ===\n{code}\n\n"
                         "今回の課題：巌が挙げた食い違いを直したコード全体を、```python で囲んで書いてください。",
                         num_predict=1500)
    except Exception as e:
        log({"phase": "fix", "name": "星月", "error": f"{type(e).__name__}: {e}"})
        print("[fix] 星月の呼び出しに失敗")
        return 0
    new_code = extract_code(fixed_text)
    if not new_code:
        print("[fix] 直したコードが取り出せなかった")
        return 0
    try:
        compile(new_code, str(path), "exec")
    except SyntaxError as e:
        post_to_memo("ムスビ（自動）", f"「{title}」の直し結果：星月のコードに構文エラー（{e}）。上書きせず中止")
        print(f"[fix] 星月のコードに構文エラー。上書きせず中止: {e}")
        return 0
    if new_code.strip() == code.strip():
        post_to_memo("星月", f"「{title}」の直し回：記録と見比べて、おかしい所は見つからなかった（コード変更なし）")
        print("[fix] 星月は「おかしい所なし」と判断（変更なし）")
        return 0

    backup = path.with_name(path.stem + ".before-fix.py")
    n = 2
    while backup.exists():
        backup = path.with_name(path.stem + f".before-fix{n}.py")
        n += 1
    backup.write_text(code, encoding="utf-8")
    path.write_text(new_code, encoding="utf-8")
    post_to_memo("星月", f"「{title}」を直した。\n```python\n{new_code}\n```")
    print(f"[fix] 上書きした（元は {backup.name} に保存）")

    status, detail = test_game(path)
    print(f"[fix] 機械テスト: {status} — {detail.splitlines()[0] if detail else ''}")
    if status != "pass":
        post_to_memo("ムスビ（自動）", f"「{title}」の直し結果：機械テスト不合格（{detail.splitlines()[0] if detail else ''}）")
        return 0

    st2, verdict2 = playtest_game(path, title, decisions)
    print(f"[fix] 直した後の試遊: {st2}")
    post_to_memo("ムスビ（自動）", f"「{title}」の直し結果：機械テスト合格、試遊の判定は {st2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
