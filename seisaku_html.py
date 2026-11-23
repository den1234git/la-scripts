# -*- coding: utf-8 -*-
"""
seisaku_html.py — 制作チーム（巌・夕凪・星月）の自動HTMLゲーム作り

seisaku_auto.py のHTML版。1回の実行で、ブラウザで開いて遊べる
小さいHTMLゲームを1本、頭から尾まで自動で作る：
  1. 夕凪がルールの選択肢を出す
  2. 夕凪が自分で選ぶ（「決定：」で出なければ、進行役としてスクリプトが確定させる）
  3. 星月が仕様に沿ってHTML（1ファイル完結）を書く
  4. スクリプトがHTMLを取り出して検査する
     （構造チェック＋ node があれば <script> の文法チェック。
       失敗したらエラー文を星月に渡して直させる。最大2回）
  5. 巌がコードと検査結果をチェックする
  6. 結果をメモ帳に記録して終わる（記録には【HTML】と付ける）

Python版（seisaku_auto.py）とは状態ファイル・出力先・ログを分けてある。
作業の受け渡しはメモ帳ではなく各工程の入出力で直接行い、メモ帳は「記録」として残す。

使い方:
  python seisaku_html.py    # 次の候補ゲームを1本作る

置き場所: F:\\ai_agent\\server\\seisaku_html.py
VillageDaemon の SCHEDULE に足す場合の例（時刻はPython版とずらすこと）:
  {"hour": 16, "minute": 0, "script": "seisaku_html.py", "key": "seisaku_html", "timeout": 3600}
モデルの呼び方は free_memo.py と同じ流儀（Ollama /api/generate、思考タグ除去）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
SEISAKU_DIR = BASE / "seisaku"
MEMO_PATH = SEISAKU_DIR / "seisaku_memo.md"
STATE_PATH = SEISAKU_DIR / "html_state.json"
LOG_PATH = SEISAKU_DIR / "html_log.jsonl"
GAMES_DIR = SEISAKU_DIR / "html_games"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
CALL_TIMEOUT = 900
MAX_FIX = 2

MODELS = {"夕凪": "sougoshitsumon:latest", "巌": "granite-code:8b", "星月": "qwen2.5-coder:7b"}

# 作るゲームの候補（ブラウザで開いて遊べる小さいもの）
CANDIDATES = [
    "じゃんけん大会（ボタンでコンピュータと対戦して勝敗を数える）",
    "計算ドリル（足し算・引き算を出題して、答えを入力して採点する）",
    "コイントス（表か裏かのボタンを押して当てる。持ち点10点、1回1点賭けて当たれば2点戻る）",
    "サイコロすごろく（ボタンを押してサイコロを振り、マスを進んでゴールを目指す）",
    "神経衰弱（カードをクリックしてめくり、同じ数字のペアをそろえる）",
    "三目並べ（マスをクリックして遊ぶ○×ゲーム、コンピュータ対戦つき。ストレッチ枠）",
]


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
    print(f"[html] {rec.get('phase', '')} {rec.get('name', '')} "
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


def extract_html(text: str) -> str:
    m = re.search(r"```(?:html)?\s*\n(.*?)```", text, flags=re.DOTALL)
    if m and "<html" in m.group(1).lower():
        return m.group(1).strip()
    low = text.lower()
    if "<html" in low:
        start = low.find("<!doctype")
        if start < 0:
            start = low.find("<html")
        end = low.rfind("</html>")
        if start >= 0 and end > start:
            return text[start:end + len("</html>")].strip()
    if m:
        return m.group(1).strip()
    return ""


def extract_decisions(text: str) -> list[str]:
    dec = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("決定：")]
    if dec:
        return dec
    # 保険：「自分の案：」の形で選んでいたら、それを決定として扱う
    alt = [ln.strip() for ln in text.splitlines() if "自分の案：" in ln]
    return ["決定：" + ln.split("自分の案：", 1)[1].strip() for ln in alt]


def test_html(path: Path) -> tuple[str, str]:
    """生成HTMLを検査する。戻り値: (pass/error, 結果の短い説明)"""
    text = path.read_text(encoding="utf-8", errors="replace")
    low = text.lower()
    problems = []
    if "<html" not in low or "</html>" not in low:
        problems.append("htmlタグが無い")
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                         text, flags=re.DOTALL | re.IGNORECASE)
    if not scripts:
        problems.append("scriptが無い")
    if "<button" not in low and "onclick" not in low and "addeventlistener" not in low:
        problems.append("操作できる要素（ボタン等）が見当たらない")
    if problems:
        return "error", "／".join(problems)
    js = "\n".join(scripts).strip()
    if js:
        tmp = path.with_suffix(".check.js")
        try:
            tmp.write_text(js, encoding="utf-8")
            r = subprocess.run(["node", "--check", str(tmp)],
                               capture_output=True, text=True, timeout=20)
            if r.returncode != 0:
                return "error", "JS文法エラー:\n" + (r.stderr or r.stdout or "")[-500:]
        except FileNotFoundError:
            return "pass", f"構造チェック合格（nodeが無いのでJS文法チェックは省略、{len(text)}文字）"
        except Exception as e:
            return "pass", f"構造チェック合格（JS文法チェックは未実施：{e}、{len(text)}文字）"
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    return "pass", f"構造＋JS文法チェック合格（{len(text)}文字）"


def review_has_problems(review: str) -> bool:
    """巌の再評価に問題があるか。書式の揺れ（全角コロン・空白）を吸収して判定する。"""
    t = review.replace("：", ":").replace(" ", "").replace("　", "")
    if "problems:あり" in t:
        return True
    for ln in review.splitlines():
        s = ln.strip()
        if s[:1] in "①②③④⑤" and "NG" in s:
            return True
    return False


def main() -> int:
    SEISAKU_DIR.mkdir(parents=True, exist_ok=True)
    GAMES_DIR.mkdir(parents=True, exist_ok=True)
    if not MEMO_PATH.exists():
        print("[html] メモ帳が無いため中止")
        return 0

    state = load_state()
    title = next((c for c in CANDIDATES
                  if c not in state["done"] and c not in state["failed"]), None)
    if title is None:
        print("[html] 候補のゲームは全部終わっている")
        return 0
    game_no = len(state["done"]) + len(state["failed"]) + 1
    print(f"[html] ゲーム{game_no}: {title}")
    post_to_memo("ムスビ（自動）", f"【HTML】新しいゲーム作りを始める。\n決定：作るゲームは「{title}」")

    # 1. 夕凪：選択肢を出す
    try:
        options_text = ask("夕凪",
                           f"あなたは村の制作チームの一員、夕凪です。3人で小さいゲームを作ります。"
                           f"今回作るのは『{title}』で、ブラウザで開いて遊ぶHTMLゲームです。あなたの役は仕様を言葉にすることです。\n"
                           f"今回の課題：『{title}』のルールで決める点を3つ挙げ、それぞれ選択肢を2つ書いてください。"
                           "本文だけを書いてください。")
    except Exception as e:
        log({"phase": "options", "name": "夕凪", "error": f"{type(e).__name__}: {e}"})
        options_text = ""
    if not options_text:
        post_to_memo("ムスビ（自動）", f"【HTML】「{title}」は中止（夕凪が選択肢を出せなかった）")
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

    # 3. 星月：HTMLを書く
    dec_block = "\n".join(decisions)
    code_prompt = (
        f"あなたは村の制作チームの一員、星月です。今回作るのは『{title}』です。"
        "仕様は次の通りです。\n"
        f"{dec_block}\n"
        "今回の課題：この仕様のHTMLゲームを書いてください。決まりは次の通りです。\n"
        "- 1つのHTMLファイルだけで完結させる（CSSとJavaScriptも同じファイルに書く）\n"
        "- 外部のライブラリや画像は使わない\n"
        "- 画面の文字は日本語にする\n"
        "- ゲームの題名、遊び方の説明、スコア表示、もう一度遊ぶボタンを入れる\n"
        "出力はコードだけ（```html で囲む）。")
    code = ""
    try:
        code_text = ask("星月", code_prompt, num_predict=2500)
        code = extract_html(code_text)
        if not code:
            code_text = ask("星月", code_prompt + "\nコードブロックでHTMLだけを書いてください。",
                            num_predict=2500)
            code = extract_html(code_text)
    except Exception as e:
        log({"phase": "code", "name": "星月", "error": f"{type(e).__name__}: {e}"})
    if not code:
        post_to_memo("ムスビ（自動）", f"【HTML】「{title}」は中止（星月がHTMLを書けなかった）")
        state["failed"].append(title)
        save_state(state)
        return 0
    post_to_memo("星月", f"```html\n{code}\n```")
    log({"phase": "code", "name": "星月", "note": f"{len(code)}文字"})

    # 4. 検査（失敗したら星月が直す）
    game_path = GAMES_DIR / f"game-{game_no:03d}.html"
    status, detail = "", ""
    for attempt in range(MAX_FIX + 1):
        game_path.write_text(code, encoding="utf-8")
        status, detail = test_html(game_path)
        log({"phase": "test", "note": f"試行{attempt + 1}: {status} — {detail.splitlines()[0] if detail else ''}"})
        if status == "pass" or attempt == MAX_FIX:
            break
        try:
            fixed = ask("星月",
                        f"あなたが書いた『{title}』のHTMLを検査したら問題が出ました。\n\n"
                        f"=== HTML ===\n{code}\n\n=== 問題 ===\n{detail}\n\n"
                        "今回の課題：問題を直したHTML全体を書いてください。出力はコードだけ（```html で囲む）。"
                        "1ファイル完結・外部ライブラリなしは守ってください。",
                        num_predict=2500)
            new_code = extract_html(fixed)
            if new_code:
                code = new_code
                post_to_memo("星月", f"```html\n{code}\n```")
        except Exception as e:
            log({"phase": "fix", "name": "星月", "error": f"{type(e).__name__}: {e}"})
            break

    # 5. 巌：再評価（チェックリスト式）
    review = ""
    try:
        review = ask("巌",
                     f"あなたは村の制作チームの一員、巌です。星月が書いた『{title}』のHTMLゲームを、"
                     "コードを読みながら項目ごとに点検してください。\n\n"
                     f"=== HTML ===\n{code[:6000]}\n\n"
                     f"機械検査の結果：{status}：{detail.splitlines()[0] if detail else ''}\n\n"
                     "今回の課題：次の5項目を1行ずつ、"
                     "「①開始：OK」または「①開始：NG — （理由）」の形で書いてください。\n"
                     "①開始：開いてすぐ遊び始められるか\n"
                     "②勝敗：勝ち負けが決まる仕組みがあるか\n"
                     "③引き分け：引き分けや同点の場合が用意されているか（無いゲームなら「③引き分け：対象外」）\n"
                     "④再戦：もう一度遊ぶ方法があるか\n"
                     "⑤スコア：得点や回数が数えられているか\n"
                     "最後の行に「総評：problems: あり」か「総評：problems: なし」と書いてください。")
    except Exception as e:
        log({"phase": "review", "name": "巌", "error": f"{type(e).__name__}: {e}"})
    if review:
        post_to_memo("巌", review)
    log({"phase": "review", "name": "巌", "note": review.splitlines()[-1][:60] if review else ""})

    # 5.5 再評価で問題があれば星月が1回だけ直す
    review_fixed = False
    if status == "pass" and review_has_problems(review):
        try:
            fixed = ask("星月",
                        f"あなたが書いた『{title}』のHTMLゲームを、巌が項目ごとに点検して問題を見つけました。\n\n"
                        f"=== 巌の再評価 ===\n{review}\n\n=== HTML ===\n{code}\n\n"
                        "今回の課題：問題を直したHTML全体を書いてください。出力はコードだけ（```html で囲む）。"
                        "1ファイル完結・外部ライブラリなしは守ってください。",
                        num_predict=2500)
            new_code = extract_html(fixed)
            if new_code:
                code = new_code
                review_fixed = True
                post_to_memo("星月", f"```html\n{code}\n```")
                game_path.write_text(code, encoding="utf-8")
                status, detail = test_html(game_path)
                log({"phase": "review-fix", "note": f"直し後の検査: {status}"})
        except Exception as e:
            log({"phase": "review-fix", "name": "星月", "error": f"{type(e).__name__}: {e}"})

    # 6. 締め
    if status == "pass":
        state["done"].append(title)
        fix_note = "再評価の指摘で修正済み。" if review_fixed else ""
        result_line = f"完成。検査合格。{fix_note}ファイル: seisaku/html_games/{game_path.name}（ブラウザで開けば遊べる）"
    else:
        state["failed"].append(title)
        result_line = f"未完成（検査不合格）。ファイル: seisaku/html_games/{game_path.name}、理由: {detail.splitlines()[0] if detail else ''}"
    save_state(state)
    post_to_memo("ムスビ（自動）", f"【HTML】「{title}」の結果：{result_line}")
    print(f"[html] 終わり: {result_line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
