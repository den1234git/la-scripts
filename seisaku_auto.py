# -*- coding: utf-8 -*-
"""
seisaku_auto.py — 制作チーム（巌・夕凪・星月）の自動ゲーム作り

1回の実行で、小さいゲームを1本、頭から尾まで自動で作る：
  1. 夕凪がルールの選択肢を出す
  2. 夕凪が自分で選ぶ（「決定：」で出なければ、進行役としてスクリプトが確定させる）
  3. 星月が仕様に沿って Python コードを書く
  4. スクリプトがコードを取り出して実際に実行し、動作テストする
     （失敗したらエラー文を星月に渡して直させる。最大2回）
  5. 巌がコードとテスト結果をチェックする
  6. 結果をメモ帳に記録して終わる

作業の受け渡しはメモ帳ではなく各工程の入出力で直接行い、メモ帳は「記録」として残す。
生成コードの実行は seisaku/games/ の中だけ、15秒で打ち切る。

使い方:
  python seisaku_auto.py    # 次の候補ゲームを1本作る

置き場所: F:\\ai_agent\\server\\seisaku_auto.py
VillageDaemon の SCHEDULE に足す場合の例:
  {"hour": 15, "minute": 0, "script": "seisaku_auto.py", "key": "seisaku_auto", "timeout": 3600}
モデルの呼び方は free_memo.py と同じ流儀（Ollama /api/generate、思考タグ除去）。
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
SEISAKU_DIR = BASE / "seisaku"
MEMO_PATH = SEISAKU_DIR / "seisaku_memo.md"
STATE_PATH = SEISAKU_DIR / "auto_state.json"
LOG_PATH = SEISAKU_DIR / "auto_log.jsonl"
GAMES_DIR = SEISAKU_DIR / "games"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
CALL_TIMEOUT = 900
TEST_TIMEOUT = 15
MAX_FIX = 2

MODELS = {"夕凪": "sougoshitsumon:latest", "巌": "granite-code:8b", "星月": "qwen2.5-coder:7b"}

# 作るゲームの候補（小さいテキストゲーム。数当ては手動で完成済みなので除く）
CANDIDATES = [
    "ハイ＆ロー（出た数字より次が大きいか小さいか当てる）",
    "じゃんけん大会（コンピュータと対戦して勝敗を数える）",
    "コイントス（表か裏かを予想して当てる。持ち点10点、1回1点賭けて当たれば2点戻る）",
    "計算ドリル（足し算・引き算を出題して採点する）",
    "サイコロすごろく（サイコロを振ってゴールを目指す）",
    "三目並べ（コンピュータと対戦する○×ゲーム。ストレッチ枠）",
]

# 動作テストで流し込む入力（色々なゲームに効く寄せ集め）
TEST_INPUTS = ["1", "2", "3", "4", "5", "h", "l", "H", "L", "y", "n",
               "はい", "いいえ", "グー", "チョキ", "パー", "0", "10", "やめる"] * 3


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def parse_reply(raw: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    if "<think>" in text:
        text = text.split("<think>")[0]
    return text.strip()


def ask(name: str, prompt: str, num_predict: int = 700) -> str:
    r = requests.post(
        OLLAMA_URL,
        json={"model": MODELS[name], "prompt": prompt, "stream": False,
              "options": {"temperature": 0.7, "num_predict": num_predict}},
        timeout=CALL_TIMEOUT,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("error"):
        raise RuntimeError(body["error"])
    return parse_reply(body.get("response", ""))


def has_repetition(text: str, window: int = 24, threshold: int = 6) -> bool:
    if len(text) < window * 2:
        return False
    seen: dict[str, int] = {}
    for i in range(0, len(text) - window + 1, 4):
        w = text[i:i + window]
        seen[w] = seen.get(w, 0) + 1
        if seen[w] >= threshold:
            return True
    return False


def post_to_memo(name: str, text: str) -> bool:
    """メモ帳へ署名つきで追記する。繰り返し・空文は載せない。"""
    if not text or has_repetition(text):
        return False
    entry = f"\n## {name} {now_str()}\n\n{text}\n"
    with MEMO_PATH.open("a", encoding="utf-8") as f:
        f.write(entry)
    return True


def log(rec: dict) -> None:
    SEISAKU_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"at": now_str(), **rec}
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[auto] {rec.get('phase', '')} {rec.get('name', '')} "
          f"{rec.get('note', '')} {rec.get('error', '')}".rstrip())


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done": [], "failed": []}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    # コードブロックが無くても、import/def を含む本文ならそのまま使う
    if "import " in text or "def " in text:
        return text.strip()
    return ""


def extract_decisions(text: str) -> list[str]:
    dec = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("決定：")]
    if dec:
        return dec
    # 保険：「自分の案：」の形で選んでいたら、それを決定として扱う
    alt = [ln.strip() for ln in text.splitlines() if "自分の案：" in ln]
    return ["決定：" + ln.split("自分の案：", 1)[1].strip() for ln in alt]


def test_game(path: Path) -> tuple[str, str]:
    """生成コードを実行して動作テストする。戻り値: (pass/error, 結果の短い説明)"""
    inp = "\n".join(TEST_INPUTS) + "\n"
    try:
        r = subprocess.run([sys.executable, str(path)], input=inp,
                           capture_output=True, text=True, timeout=TEST_TIMEOUT,
                           cwd=str(path.parent))
        out, err = r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        if out.strip() and "SyntaxError" not in err and "IndentationError" not in err:
            return "pass", f"時間切れまで動作（出力 {len(out)}文字）"
        return "error", "時間切れ（出力なし、または文法エラー）\n" + err[-500:]
    if "SyntaxError" in err or "IndentationError" in err:
        return "error", err[-500:]
    if "Traceback" in err:
        if "EOFError" in err and out.strip():
            return "pass", f"入力が尽きるまで動作（出力 {len(out)}文字）"
        return "error", err[-500:]
    if not out.strip():
        return "error", "出力が無かった"
    return "pass", f"正常終了（出力 {len(out)}文字）"


def clean_input(line: str) -> str:
    """試遊の入力を整える。文で返ってきたら、最初の短い英数字トークンを抜く
    （例：「じゃあ「1」です。」→「1」、「1. 何のために学びたい？」→「1」）。"""
    line = line.strip()
    if len(line) > 3:
        m = re.search(r"(?<![0-9A-Za-z])([0-9A-Za-z]{1,3})(?![0-9A-Za-z])", line)
        if m:
            return m.group(1)
    return line


def playtest_game(path: Path, title: str, decisions: list[str]) -> tuple[str, str]:
    """夕凪が実際にゲームを繰り返し遊ぶ（1回勝負のゲームは最大10回、合計30手まで）。
    戻り値: (ok / problem / skip, 判定文)"""
    path = path.resolve()  # 相対パス＋cwd変更で「ファイルが開けない」事故を防ぐ
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"  # 子プロセスの出力をUTF-8にそろえる（文字化け防止）
    env["PYTHONUTF8"] = "1"
    dec_block = "\n".join(decisions)
    transcript = ""
    total_moves = 0
    stopped = False
    deadline = time.time() + 600

    for session in range(1, 11):
        if stopped or total_moves >= 30 or time.time() > deadline:
            break
        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", str(path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", cwd=str(path.parent), env=env)
        except Exception as e:
            if session == 1:
                return "skip", f"起動できなかった: {e}"
            break
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
        transcript += f"\n―――― {session}回目 ――――\n"
        session_moves = 0
        try:
            while session_moves < 12 and total_moves < 30 and time.time() < deadline:
                chunk = ""
                idle_deadline = time.time() + 10
                quiet_until = time.time() + 1.5
                while time.time() < idle_deadline:
                    try:
                        c = q.get(timeout=0.2)
                        chunk += c
                        quiet_until = time.time() + 1.5
                    except queue.Empty:
                        if chunk and time.time() >= quiet_until:
                            break
                        if proc.poll() is not None and not chunk:
                            break
                transcript += chunk
                if proc.poll() is not None:
                    break
                try:
                    reply = ask("夕凪",
                                f"あなたは村の制作チームの一員、夕凪です。星月が作ったゲーム『{title}』を試しに遊んでいます（{session}回目）。\n"
                                f"仕様（決めたルール）：\n{dec_block}\n\n"
                                f"ここまでの画面：\n{transcript[-1500:]}\n\n"
                                "今回の課題：ゲームが次に入力を待っているなら、入力する文字だけを短く1行で書いてください。"
                                "説明や文は書かないこと。もう十分遊んだ、または進めなくなったと思ったら「おわり」とだけ書いてください。",
                                num_predict=60)
                except Exception as e:
                    log({"phase": "play", "name": "夕凪", "error": f"{type(e).__name__}: {e}"})
                    stopped = True
                    break
                line = reply.strip().splitlines()[0].strip() if reply.strip() else ""
                line = clean_input(line)
                if len(line) > 10 and line != "おわり":
                    # 長い文が返ってきたら、短い入力だけをもう一度聞く
                    try:
                        reply2 = ask("夕凪",
                                     "さっきの返事は長すぎて、ゲームへの入力にできません。"
                                     "入力する文字だけを、短く1行で書いてください（例：1）。",
                                     num_predict=30)
                        line2 = reply2.strip().splitlines()[0].strip() if reply2.strip() else ""
                        if line2:
                            line = clean_input(line2)
                    except Exception:
                        pass
                if line == "おわり" or proc.poll() is not None:
                    stopped = True
                    break
                transcript += f"\n> {line}\n"
                try:
                    proc.stdin.write(line + "\n")
                    proc.stdin.flush()
                except Exception:
                    break
                session_moves += 1
                total_moves += 1
        finally:
            try:
                proc.kill()
            except Exception:
                pass

    if not transcript.strip():
        return "skip", "画面の記録が取れなかった"
    try:
        path.with_name(path.stem + ".play.txt").write_text(transcript, encoding="utf-8")
    except Exception:
        pass
    try:
        verdict = ask("夕凪",
                      f"あなたは村の制作チームの一員、夕凪です。ゲーム『{title}』を繰り返し遊んだ記録です。\n\n"
                      f"{transcript[-5000:]}\n\n"
                      f"仕様（決めたルール）：\n{dec_block}\n\n"
                      "今回の課題：遊んだ結果を報告してください。1行目に「試遊：遊べた」か"
                      "「試遊：おかしい点あり」のどちらかを書き、2行目に理由を短く書いてください。"
                      "何回か遊んだ中で一度でもおかしい結果（勝敗の食い違いなど）があれば、それを理由に書いてください。"
                      "記録の中に Traceback（エラー終了） が1つでもあれば、必ず「試遊：おかしい点あり」と書いてください。")
    except Exception as e:
        log({"phase": "play-verdict", "name": "夕凪", "error": f"{type(e).__name__}: {e}"})
        return "skip", "判定が取れなかった"
    post_to_memo("夕凪", verdict)
    log({"phase": "play", "name": "夕凪", "note": verdict.splitlines()[0] if verdict else ""})
    if "おかしい点あり" in verdict:
        return "problem", verdict
    return "ok", verdict


def main() -> int:
    SEISAKU_DIR.mkdir(parents=True, exist_ok=True)
    GAMES_DIR.mkdir(parents=True, exist_ok=True)
    if not MEMO_PATH.exists():
        print("[auto] メモ帳が無いため中止")
        return 0

    state = load_state()
    title = next((c for c in CANDIDATES
                  if c not in state["done"] and c not in state["failed"]), None)
    if title is None:
        print("[auto] 候補のゲームは全部終わっている")
        return 0
    game_no = len(state["done"]) + len(state["failed"]) + 1
    print(f"[auto] ゲーム{game_no}: {title}")
    post_to_memo("ムスビ（自動）", f"新しいゲーム作りを始める。\n決定：作るゲームは「{title}」")

    # 1. 夕凪：選択肢を出す
    try:
        options_text = ask("夕凪",
                           f"あなたは村の制作チームの一員、夕凪です。3人で小さいゲームを作ります。"
                           f"今回作るのは『{title}』です。あなたの役は仕様を言葉にすることです。\n"
                           f"今回の課題：『{title}』のルールで決める点を3つ挙げ、それぞれ選択肢を2つ書いてください。"
                           "本文だけを書いてください。")
    except Exception as e:
        log({"phase": "options", "name": "夕凪", "error": f"{type(e).__name__}: {e}"})
        options_text = ""
    if not options_text:
        post_to_memo("ムスビ（自動）", f"「{title}」は中止（夕凪が選択肢を出せなかった）")
        state["failed"].append(title)
        save_state(state)
        return 0
    post_to_memo("夕凪", options_text)
    log({"phase": "options", "name": "夕凪", "note": f"{len(options_text)}文字"})

    # 2. 夕凪：自分で選ぶ
    try:
        decide_text = ask("夕凪",
                          f"あなたは村の制作チームの一員、夕凪です。『{title}』のルールについて、"
                          "さっき自分が出した選択肢は次の通りです。\n\n"
                          f"{options_text}\n\n"
                          "今回の課題：3点それぞれ、選択肢から1つずつ選んでください。"
                          "質問は書かないでください。決めたことは3行とも「決定：」で始めて書いてください。")
    except Exception as e:
        log({"phase": "decide", "name": "夕凪", "error": f"{type(e).__name__}: {e}"})
        decide_text = ""
    post_to_memo("夕凪", decide_text)
    decisions = extract_decisions(decide_text)
    if not decisions:
        decisions = ["決定：細部は作り手の判断に任せる（夕凪が決め切れなかったため）"]
    log({"phase": "decide", "name": "夕凪", "note": f"決定 {len(decisions)}件"})

    # 3. 星月：コードを書く
    dec_block = "\n".join(decisions)
    code_prompt = (
        f"あなたは村の制作チームの一員、星月です。今回作るのは『{title}』です。"
        "仕様は次の通りです。\n"
        f"{dec_block}\n"
        "今回の課題：この仕様のPythonプログラムを書いてください。"
        "出力はコードだけ（```python で囲む）。標準入力と標準出力だけで遊べるものにしてください。")
    code = ""
    try:
        code_text = ask("星月", code_prompt, num_predict=1500)
        code = extract_code(code_text)
        if not code:
            code_text = ask("星月", code_prompt + "\nコードブロックでコードだけを書いてください。",
                            num_predict=1500)
            code = extract_code(code_text)
    except Exception as e:
        log({"phase": "code", "name": "星月", "error": f"{type(e).__name__}: {e}"})
    if not code:
        post_to_memo("ムスビ（自動）", f"「{title}」は中止（星月がコードを書けなかった）")
        state["failed"].append(title)
        save_state(state)
        return 0
    post_to_memo("星月", f"```python\n{code}\n```")
    log({"phase": "code", "name": "星月", "note": f"{len(code)}文字"})

    # 4. 動作テスト（失敗したら星月が直す）
    game_path = GAMES_DIR / f"game-{game_no:03d}.py"
    status, detail = "", ""
    for attempt in range(MAX_FIX + 1):
        game_path.write_text(code, encoding="utf-8")
        status, detail = test_game(game_path)
        log({"phase": "test", "note": f"試行{attempt + 1}: {status} — {detail.splitlines()[0] if detail else ''}"})
        if status == "pass" or attempt == MAX_FIX:
            break
        try:
            fixed = ask("星月",
                        f"あなたが書いた『{title}』のコードを実行したらエラーが出ました。\n\n"
                        f"=== コード ===\n{code}\n\n=== エラー ===\n{detail}\n\n"
                        "今回の課題：エラーを直したコード全体を書いてください。出力はコードだけ（```python で囲む）。",
                        num_predict=1500)
            new_code = extract_code(fixed)
            if new_code:
                code = new_code
                post_to_memo("星月", f"```python\n{code}\n```")
        except Exception as e:
            log({"phase": "fix", "name": "星月", "error": f"{type(e).__name__}: {e}"})
            break

    # 5. 巌：チェック
    review = ""
    try:
        review = ask("巌",
                     f"あなたは村の制作チームの一員、巌です。星月が書いた『{title}』のコードと、"
                     f"動作テストの結果（{status}：{detail.splitlines()[0] if detail else ''}）を確認してください。\n\n"
                     f"=== コード ===\n{code[:4000]}\n\n"
                     "今回の課題：問題があれば「problems: あり — （内容を1行）」、"
                     "なければ「problems: なし」とだけ書いてください。")
    except Exception as e:
        log({"phase": "review", "name": "巌", "error": f"{type(e).__name__}: {e}"})
    if review:
        post_to_memo("巌", review)
    log({"phase": "review", "name": "巌", "note": review[:60]})

    # 5.5 試遊（テスト合格のときだけ。おかしい点があれば星月が1回だけ直す）
    play_status = ""
    if status == "pass":
        play_status, play_detail = playtest_game(game_path, title, decisions)
        if play_status == "problem":
            try:
                try:
                    play_log_path = game_path.with_name(game_path.stem + ".play.txt")
                    play_log = play_log_path.read_text(encoding="utf-8", errors="replace")[-2000:] if play_log_path.exists() else ""
                except Exception:
                    play_log = ""
                fixed = ask("星月",
                            f"あなたが書いた『{title}』のゲームを、夕凪が試しに遊んでおかしい点を見つけました。\n\n"
                            f"=== 夕凪の報告 ===\n{play_detail}\n\n=== 試遊の記録 ===\n{play_log}\n\n=== コード ===\n{code}\n\n"
                            "今回の課題：夕凪の報告は手がかりです。試遊の記録の実際の動きとコードを見比べて、"
                            "おかしい点を直したコード全体を書いてください。判定（勝敗・スコア）が絡む所は、"
                            "感覚で読まず、全ての組み合わせを表にしてルールと照合してから直してください。"
                            "出力はコードだけ（```python で囲む）。",
                            num_predict=1500)
                new_code = extract_code(fixed)
                if new_code:
                    code = new_code
                    post_to_memo("星月", f"```python\n{code}\n```")
                    game_path.write_text(code, encoding="utf-8")
                    status, detail = test_game(game_path)
                    log({"phase": "play-fix", "note": f"直し後のテスト: {status}"})
            except Exception as e:
                log({"phase": "play-fix", "name": "星月", "error": f"{type(e).__name__}: {e}"})

    # 6. 締め
    if status == "pass":
        state["done"].append(title)
        play_note = {"ok": "試遊でも問題なし。", "problem": "試遊の指摘で修正済み。", "skip": "試遊は記録なし。"}.get(play_status, "")
        result_line = f"完成。動作テスト合格。{play_note}ファイル: seisaku/games/{game_path.name}"
    else:
        state["failed"].append(title)
        result_line = f"未完成（動作テスト不合格）。ファイル: seisaku/games/{game_path.name}、理由: {detail.splitlines()[0] if detail else ''}"
    save_state(state)
    post_to_memo("ムスビ（自動）", f"「{title}」の結果：{result_line}")
    print(f"[auto] 終わり: {result_line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
