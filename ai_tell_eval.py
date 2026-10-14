# -*- coding: utf-8 -*-
# style-guard:off
"""
용도: 판정기(ai_tell_judge.py)가 사람 글과 AI 글을 실제로 가르는지 재는 시험.
      판정 지시문이나 등급 문턱값(GRADE_CUTS)을 고치면 이걸 다시 돌려 내 글 오판이 0 인지 본다.
실행: python ai_tell_eval.py [--fresh]
      --fresh 는 AI 에게 규칙 없이 글 여섯 편을 새로 쓰게 해 data/eval/ 에 둔다(학습의 오탐 시험도 이 글을 쓴다).
입력: AI_TELL_HOME/samples/*.txt (내가 쓴 글. 긴 글은 4,000자씩 최대 세 토막으로 나눠 본다)
      examples/ai_*.txt 와 data/eval/ai_*.txt (AI 가 쓴 글)
출력: 표(글마다 확실한 의심 수, 1,000자당 수, 등급) + data/eval/last.json
외부 의존: claude CLI. 글 한 토막에 판정 호출 한 번.
일부러 안 만든 것: 문턱값 자동 조정. 숫자를 보고 사람이 정한다.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ai_tell_judge as J  # noqa: E402

EVAL = J.DATA / "eval"
EXAMPLES = Path(__file__).resolve().parent / "examples"
PIECE = 4000
TOPICS = [
    ("plain", "작은 가게 사장이 단골을 만드는 방법"),
    ("plain", "처음 온 손님이 가게 소개 글 첫 문단에서 나가는 이유"),
    ("plain", "혼자 일하는 사람이 하루 일정을 짜는 법"),
    ("polite", "새 직원에게 일을 넘길 때 지켜야 할 순서"),
    ("polite", "견적서를 늦게 보내면 생기는 문제"),
    ("polite", "고객 후기를 꾸준히 모으는 방법"),
]
WRITE = "{topic}\n\n위 주제로 칼럼을 한국어로 1,800자 안팎으로 써라. 말투는 {tone}. 소제목 3개. 본문만 출력한다."


def human_cases():
    out = []
    for f in J.sample_files():
        text = f.read_text(encoding="utf-8-sig")
        if J.count_sent(text) < J.JUDGE_MIN_SENT:
            continue
        if len(text) <= PIECE * 1.5:
            out.append((f"사람 {f.stem[:14]}", text, f.name))
            continue
        step = max((len(text) - PIECE) // 2, 1)
        for i, a in enumerate(range(0, len(text) - PIECE + 1, step)):
            if i < 3:
                out.append((f"사람 {f.stem[:10]} {a}", text[a:a + PIECE], f.name))
    return out


def ai_cases(fresh=False):
    EVAL.mkdir(parents=True, exist_ok=True)
    if fresh:
        for i, (tone, topic) in enumerate(TOPICS):
            role = "sonnet" if i % 2 == 0 else "opus"
            text, _ = J.call_claude(WRITE.format(topic=topic, tone="평어체(~다)" if tone == "plain" else "합쇼체(~습니다)"),
                                    role=role, timeout=240, system="너는 칼럼을 쓰는 작가다.")
            (EVAL / f"ai_{i}_{tone}.txt").write_text(text, encoding="utf-8")
    out = []
    for base in (EXAMPLES, EVAL):
        for f in sorted(base.glob("ai_*.txt")):
            out.append((f"AI {f.stem[:16]}", f.read_text(encoding="utf-8-sig"), ""))
    return out


def main():
    humans = human_cases()
    if not humans:
        print(f"내 글 표본이 없습니다. {J.SAMPLES} 에 직접 쓴 글(.txt, {J.JUDGE_MIN_SENT}문장 이상)을 넣고 다시 돌리세요.")
        print("표본 없이 AI 글만 판정합니다.\n")
    cases = humans + ai_cases("--fresh" in sys.argv)

    def one(c):
        name, text, ex = c
        try:
            v = J.judge(text, exclude=ex)
            return {"name": name, "chars": len(text), "strong": v["strong"], "weak": v["weak"], "per1k": v["per1k"],
                    "grade": v["grade"], "top": [s["quote"][:40] + " | " + s["rule"] for s in v["suspects"][:4] if s["level"] == "강"]}
        except Exception as e:  # noqa: BLE001
            return {"name": name, "error": str(e)[:200]}

    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(one, cases))
    for r in rows:
        if "error" in r:
            print(f"{r['name']:26s} 실패 {r['error']}")
            continue
        print(f"{r['name']:26s} {r['chars']:5d}자  강 {r['strong']:2d}  약 {r['weak']:2d}  1000자당 {r['per1k']:.2f}  {r['grade']}")
        for t in r["top"]:
            print("      ", t)
    (EVAL / "last.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    hb = [r for r in rows if r["name"].startswith("사람") and r.get("grade") in J.BLOCK_GRADES]
    ab = [r for r in rows if r["name"].startswith("AI") and r.get("grade") in J.BLOCK_GRADES]
    nh = sum(1 for r in rows if r["name"].startswith("사람") and "grade" in r)
    na = sum(1 for r in rows if r["name"].startswith("AI") and "grade" in r)
    print(f"\n내 글을 AI 로 본 것 {len(hb)}/{nh}, AI 글을 잡은 것 {len(ab)}/{na}")
    if hb:
        print("내 글이 잡혔습니다. keep.txt 에 내 말버릇을 적거나, 표본을 더 넣거나, GRADE_CUTS 의 문턱을 올리세요.")


if __name__ == "__main__":
    main()
