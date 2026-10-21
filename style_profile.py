# -*- coding: utf-8 -*-
# style-guard:off  (측정 도구. 산문 없음)
"""
용도: 한국어 산문의 문장 통계 지문(문장 길이 분포, 쉼표, 명령형)을 재고, 기준 표본의 허용 범위와 비교한다.
      writing_style_guard.py 가 불러 쓴다. 단독 실행하면 표본으로 기준 파일을 만들거나 글 하나를 채점한다.
        만들기: python style_profile.py build <표본이름> 표본.txt [표본2.txt ...]
                → AI_TELL_HOME/style_profile.json 에 이름별로 저장(말투별 표본 여러 개)
        채점:   python style_profile.py score 글.txt
        기준 보기: python style_profile.py bands
      기준 파일이 없으면 이 검사는 건너뛴다. 검사할 글의 합쇼체 비율에 가까운 표본과 비교한다.
      표본을 넣은 뒤엔 그 표본 자체와 AI 초안 하나를 가드에 넣어 표본은 통과하고 AI 초안은 막히는지 확인한다.
왜 숫자인가: 패턴 규칙은 낱말을 바꾸면 피해 가지만 문장 길이 분포와 쉼표 빈도는 글 전체 습관이라
      AI 글이 가장 먼저 어긋나는 곳이다(만든 사람의 실측: AI 글 평균 29자·16자 이하 25%, 사람 글 37~53자·2~9%).
일부러 안 만든 것: 문단 길이, 굵게·따옴표 같은 서식.
"""
from __future__ import annotations

import json
import os
import re
import statistics as st
import sys
from pathlib import Path

PROFILE = Path(os.environ.get("AI_TELL_HOME") or Path.home() / ".ai-tell").expanduser() / "style_profile.json"
SENT = re.compile(r"[가-힣][^.!?\n]{5,}?(?:니다|세요|십시오|니까|[가-힣]다|[가-힣]요|[가-힣]죠)[.!?]")
MIN_SENTENCES = 30  # 이보다 짧은 글은 분포가 불안정해 채점하지 않는다

# 지문 항목: (이름, 계산, 방향) 방향 'min' = 기준보다 작으면 위반, 'max' = 크면 위반
def fingerprint(text: str) -> dict:
    sents = [m.group(0).strip() for m in SENT.finditer(text)]
    n = len(sents)
    if n == 0:
        return {"n": 0}
    L = [len(s) for s in sents]
    joined = "\n".join(sents)
    share = lambda pred: round(100 * sum(1 for s in sents if pred(s)) / n, 1)
    per100 = lambda rx: round(100 * len(re.findall(rx, joined)) / n, 1)
    return {
        "n": n,
        "len_mean": round(st.mean(L), 1),
        "short16": share(lambda s: len(s) <= 16),
        "short25": share(lambda s: len(s) <= 25),
        "long60": share(lambda s: len(s) >= 60),
        "comma": round(sum(s.count(",") for s in sents) / n, 2),
        "imperative": per100(r"(?:하세요|보세요|마세요|두세요|챙기세요|넣으세요|확인하세요|해보세요|해 보세요)[.!]"),
        "honorific": per100(r"사장님|여러분"),
        "nida": share(lambda s: re.search(r"(?:니다|세요|십시오|니까)[.!?]$", s) is not None),  # 합쇼체 비율. 표본 고르기에 씀
    }


# 허용 범위 = 표본 수치에서 계산한다. 표본을 바꾸면(style_profile.py build 새표본.txt) 기준도 따라 바뀐다.
#   (항목, 방향, 표본값→기준 계산, 설명)  방향 'min' = 기준보다 작으면 위반, 'max' = 크면 위반
BAND_RULES = [
    ("len_mean",   "min", lambda v: round(v * 0.70, 1),  "평균 문장 길이가 표본보다 많이 짧음. 짧은 문장 둘을 이어 한 문장으로"),
    ("short16",    "max", lambda v: round(v + 12, 1),    "16자 이하 토막 문장이 표본보다 많음. 단정 연타를 앞 문장에 붙이기"),
    ("short25",    "max", lambda v: round(v + 20, 1),    "25자 이하 문장 비율이 표본보다 높음"),
    ("long60",     "min", lambda v: round(v * 0.35, 1),  "60자 넘는 긴 문장이 표본보다 훨씬 적음. 이유·조건을 연결어미로 이어 붙이기"),
    ("comma",      "min", lambda v: round(v * 0.30, 2),  "문장당 쉼표가 표본보다 적음. 토막 문장을 쉼표로 잇기"),
    ("imperative", "max", lambda v: round(v + 10, 1),    "명령형 종결(~하세요)이 표본보다 잦음"),
]


def load_profiles() -> list[dict]:
    if not PROFILE.exists():
        return []
    d = json.loads(PROFILE.read_text(encoding="utf-8"))
    return d.get("profiles", [d]) if isinstance(d, dict) else []


def pick_profile(fp: dict) -> dict | None:
    """말투 갈래(평어체/합쇼체)를 먼저 맞추고, 그 안에서 문장이 가장 많은 표본과 비교한다.
    문장 수십 개짜리 표본이 수천 개짜리 표본을 이기면 허용 범위가 그 한 편의 습관으로 좁아진다."""
    profs = load_profiles()
    if not profs:
        return None
    nida = fp.get("nida", 0)
    same = [pr for pr in profs if (pr.get("nida", 0) >= 50) == (nida >= 50)]
    if same:
        return max(same, key=lambda pr: pr.get("n", 0))
    return min(profs, key=lambda pr: abs(pr.get("nida", 0) - nida))


def bands(ref: dict) -> dict:
    out = {}
    for k, direction, f, msg in BAND_RULES:
        if k in ref:
            out[k] = (direction, f(ref[k]), msg)
    return out


# 어긋난 항목을 몇 개로 칠지. 서로 따라 움직이는 항목은 한 묶음으로 센다(im-not-ai metrics.py 의 '쉼표 계열 최대 3점'과 같은 생각).
# 16자 이하와 25자 이하는 같은 토막 문장을 두 번 센다.
POINT_GROUP = {"short16": "short", "short25": "short"}
MIN_POINTS = 2  # 1개만 어긋나도 막으면 만든 사람의 블로그 글 86편 중 9편이 막혔다(2개면 0편). AI 글은 18편 중 9편 → 6편만 잡히고,
                # 빠진 것은 뒤에 붙은 읽는 판정(ai_tell_judge)이 맡는다.


def points(hits: list[str]) -> int:
    keys = {h.split("=")[0].replace("지문 ", "") for h in hits}
    return len({POINT_GROUP.get(k, k) for k in keys})


def score(text: str) -> list[str]:
    """막을 만큼 어긋났을 때만 항목을 돌려준다(묶음으로 센 수가 MIN_POINTS 이상)."""
    hits = score_all(text)
    return hits if points(hits) >= MIN_POINTS else []


def score_all(text: str) -> list[str]:
    """어긋난 항목 전부(막을지와 무관). 보고용."""
    fp = fingerprint(text)
    if fp.get("n", 0) < MIN_SENTENCES:
        return []
    ref = pick_profile(fp)
    if not ref:
        return []
    hits = []
    for k, (direction, limit, msg) in bands(ref).items():
        v = fp[k]
        if (direction == "min" and v < limit) or (direction == "max" and v > limit):
            hits.append(f"지문 {k}={v} (기준 {'≥' if direction == 'min' else '≤'}{limit}, 표본 '{ref.get('name')}' {ref.get(k)}): {msg}")
    return hits


def main(argv: list[str]) -> None:
    if len(argv) >= 4 and argv[1] == "build":
        name, files = argv[2], argv[3:]
        text = "\n".join(Path(p).read_text(encoding="utf-8-sig") for p in files)
        fp = fingerprint(text)
        fp["name"] = name
        fp["_sources"] = [Path(p).name for p in files]
        profs = [pr for pr in load_profiles() if pr.get("name") != name] + [fp]
        PROFILE.parent.mkdir(parents=True, exist_ok=True)
        PROFILE.write_text(json.dumps({"profiles": profs}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(fp, ensure_ascii=False))
    elif len(argv) >= 2 and argv[1] == "bands":
        for pr in load_profiles():
            print(pr.get("name"), json.dumps({k: v[:2] for k, v in bands(pr).items()}, ensure_ascii=False))
    elif len(argv) >= 3 and argv[1] == "score":
        text = Path(argv[2]).read_text(encoding="utf-8-sig")
        fp = fingerprint(text); ref = pick_profile(fp)
        print(json.dumps(fp, ensure_ascii=False), "→ 비교 표본:", ref.get("name") if ref else None)
        for h in score(text):
            print("-", h)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
