# -*- coding: utf-8 -*-
# style-guard:off  (이 파일 자체는 금지 문형을 예시로 담고 있어 검사에서 뺀다)
"""
용도: 사람이 읽는 한국어 산문(전자책·칼럼·안내문)을 파일이나 스크립트 안에 새로 써 넣을 때,
      AI 티 나는 문형과 금지 단어가 있으면 도구 호출을 막고 목록을 돌려준다.
      규칙은 im-not-ai(MIT)의 quick-rules.md 분류(A·C·D·E·I·J) 가운데 기계로 잡히는 것만 옮겼다.
실행 시점: Claude Code / Codex 의 PreToolUse (Write, Edit, MultiEdit, NotebookEdit, apply_patch, Bash 의 heredoc).
입력: 도구 호출 내용(stdin JSON). 새로 들어가는 글자만 본다.
출력: 한국어 문장이 5개 이상 들어 있고 위반이 있으면 차단 판정 + 사유. 아니면 통과.
왜 도구 직전인가: 원고가 답변이 아니라 스크립트 안 문자열로 쓰이면 답변 검사를 통째로 비껴간다.
      글이 어디에 쓰이든 파일에 닿는 순간 잡는다.
끄는 법: 본문에 style-guard:off 를 넣거나 환경변수 STYLE_GUARD=0. 오탐이면 이 파일의 목록을 고친다.
금지 단어: AI_TELL_HOME 의 ban_words.txt (한 줄에 한 낱말)가 있으면 그것을 쓰고, 없으면 아래 기본 목록을 쓴다.
임계 횟수: 만든 사람의 글 수백 편을 재서 "사람도 이만큼은 쓴다"보다 위에 둔 값이다. 자기 글에서 오탐이 나면 올린다.
일부러 안 만든 것:
  - 사람 글(사용자 원문)을 옮겨 적는 경우 구분. 못 한다. style-guard:off 로 넘긴다.
  - 문장 단위 윤문. 고치는 건 모델이 한다. 여긴 잡기만.
어떤 오류가 나도 막지 않고 통과(exit 0).
"""
from __future__ import annotations

import json
import os
import re
import sys
# Windows 는 stdin 이 cp949 라 한국어 JSON 해석이 실패해 "오류면 통과"로 조용히 빠진다. 항상 utf-8 로 읽는다.
try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from datetime import datetime
from pathlib import Path

BLOCK_CODE = 2
AI_HOME = Path(os.environ.get("AI_TELL_HOME") or Path.home() / ".ai-tell").expanduser()
LOG = AI_HOME / "data" / "style-violations.log"
MIN_SENTENCES = 5

EXEMPT_PATH = re.compile(r"writing_style_guard\.py$|\.(log|jsonl|lock)$|[\\/]samples[\\/]", re.I)  # samples/ = 사용자 원문 표본

# 한국어 산문 문장 하나: 한글로 시작해 종결어미 + 마침표/물음표로 끝나는 덩어리
SENT = re.compile(r"[가-힣][^.!?\n]{5,}?(?:니다|세요|십시오|니까|[가-힣]다|[가-힣]요)[.!?]")  # 짧은 단정도 문장으로 세야 3연타가 잡힌다

# 금지 단어. AI 가 뜻이 흐린 채로 자주 쓰는 추상 명사다. 앞뒤에 한글이 붙은 합성어(빈칸·안전장치·축의금)는 안 잡는다.
DEFAULT_BAN = ["칸", "값", "묶음", "쌍", "시험대", "자리", "걸음", "장치", "축"]


def _ban_words() -> list:
    try:
        words = [w.strip() for w in (AI_HOME / "ban_words.txt").read_text(encoding="utf-8-sig").splitlines()]
        words = [w for w in words if w and not w.startswith("#")]
        return words or DEFAULT_BAN
    except Exception:
        return DEFAULT_BAN


BAN = re.compile(r"(?<![가-힣A-Za-z])(" + "|".join(re.escape(w) for w in _ban_words())
                 + r")(?:[이가은는을를의에도과와로]|으로)?(?![가-힣A-Za-z])")

# (이름, 정규식, 임계 횟수, 고치는 법)
RULES = [
    # 임계는 사람 글 실측 최대치보다 위에 둔다.
    ("D-3 열거 도입구", re.compile(r"읽는 법은 이렇습니다|정리하면 이렇습니다|다음과 같습니다|크게 .{1,3} 가지로"), 1, "도입구를 지우고 바로 본론으로"),
    ("D-1 결산 관용구", re.compile(r"결론적으로|정리하자면|요약하면|한마디로"), 2, "지우고 결론 문장만"),  # 사람도 글 한 편에 한 번은 쓴다
    ("D-6 결말 공식", re.compile(r"할 때입니다|시점입니다|순간입니다"), 1, "구체 동사로 끝내기"),
    ("메타 발화(글이 자기를 설명)", re.compile(r"이 (?:책|장|파트|글|문서|절)(?:이|의|은|는|에서는?) [^.]{0,25}(?:효력|관통|다룹니다|설명합니다|이야기합니다|말씀드립니다)|효력을 (?:갖|가지)"), 1, "그 문장을 빼거나 독자가 할 일로 바꾸기"),
    # 일부러 뺀 규칙: 사람 글 수백 편을 재 보니 사람도 흔히 쓰는 꼴이었다.
    #   A-7 '가지고 있다', C-8 'A가 아니라 B', A-1 '~에 대해'
    #   자기 글에 이 꼴이 거의 없다면 RULES 에 다시 넣어도 된다.
    ("'~게 됩니다' AI 문형", re.compile(r"게 (?:됩니다|된다|되었습니다|됐습니다|될 것입니다)"), 3, "그냥 동사로('메꿔 내게 됩니다'→'메꿔 냅니다')"),
    ("예고 문형(뒤에서 다룬다)", re.compile(r"(?:다음|뒤|아래|이어지는) (?:절|장|파트|부분)에서 [^.\n]{0,20}(?:다룹니다|설명합니다|이야기합니다|보겠습니다|알아보겠습니다|말씀드리겠습니다|확인합니다)|뒤에서 다루"), 1, "예고를 빼고 지금 문장에서 독자가 할 일로"),
    ("A-8 이중 피동", re.compile(r"되어집니다|되어진다|지게 됩니다|어지기 때문"), 1, "능동이나 단일 피동으로"),
    ("명사 종결 '~는 길/몫입니다'", re.compile(r"(?:는|하는|의) (?:길|몫)입니다"), 1, "동사로 끝내기"),
    ("I-1 '것입니다' 연타", re.compile(r"것입니다[.!]"), 5, "일부를 '~합니다'로"),
    ("J 대시·불릿 기호", re.compile(r"[—–•▶]"), 1, "쉼표·마침표·줄바꿈으로"),
    ("같은 틀 항목 반복 '**라벨**(설명):'", re.compile(r"\*\*[^*\n]{2,30}\*\*\([^)\n]{2,40}\):"), 3, "상황 A/B 처럼 문장으로 풀거나 틀을 깨기"),
    # 셋씩 맞춘 나열. 사람 글 실측은 문장의 1~2% 수준이라 3회 이상 + 5% 이상일 때만 잡는다.
    ("3항 병렬 나열 강박", re.compile(r"[가-힣A-Za-z0-9)]{2,10}, ?[가-힣A-Za-z0-9)]{2,10}, ?(?:그리고 )?[가-힣A-Za-z0-9)]{2,10}(?:[을를이가은는와과]|입니다|이다)"), 3, "둘로 줄이거나 한 항목을 문장으로 풀기"),
]
# 문장 단위(SENT)로는 안 잡히는 종결이라 전체 글에서 본다
NOUN_Q_END = re.compile(r"[가-힣](?:인가|는가|일까)\.")


def deny(reason: str) -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        "decision": "block",
        "reason": reason,
        "systemMessage": "글쓰기 문체 가드: 차단",
    }
    print(json.dumps(out, ensure_ascii=False))
    print(reason, file=sys.stderr)
    sys.exit(BLOCK_CODE)


NL = chr(10)


def added_text(data: dict) -> tuple[str, str]:
    tool = data.get("tool_name") or data.get("toolName") or ""
    ti = data.get("tool_input") or data.get("toolInput") or {}
    path = str(ti.get("file_path") or ti.get("path") or "")
    parts = []
    if tool == "Bash":
        parts.append(str(ti.get("command") or ""))
    elif tool == "apply_patch":
        # Codex 는 패치 글을 command 에 담아 준다(0.156 실측). 새로 들어가는 줄(+)만 본다.
        patch = str(ti.get("patch") or ti.get("input") or ti.get("command") or "")
        parts.append(NL.join(l[1:] for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++")))
        if not path:
            m = re.search(r"^[*]{3} (?:Add|Update) File: (.+)$", patch, re.M)
            path = m.group(1).strip() if m else ""
    else:
        for k in ("content", "new_string", "new_str", "new_source", "patch"):
            v = ti.get(k)
            if v: parts.append(str(v))
        for e in ti.get("edits") or []:
            if isinstance(e, dict) and e.get("new_string"):
                parts.append(str(e["new_string"]))
    return tool, "\n".join(parts), path


def check(text: str) -> list[str]:
    sents = [m.group(0) for m in SENT.finditer(text)]
    if len(sents) < MIN_SENTENCES:
        return []
    prose = "\n".join(sents)
    hits: list[str] = []
    ban = {}
    for m in BAN.finditer(prose):
        ban[m.group(1)] = ban.get(m.group(1), 0) + 1
    # 사람도 이 말들을 뜻 그대로 쓴다. "두 칸으로 본다", "정당한 값을 치르고", "답을 주는 자리"는 정상 한국어다.
    # 그래서 한 글에 같은 말이 4회 이상 + 60문장당 1회 이상일 때만 잡는다.
    ban = {w: c for w, c in ban.items() if c >= 4 and c / len(sents) >= 1 / 60}
    if ban:
        hits.append("금지 단어(사업 문서 문체 기준): " + ", ".join(f"'{w}' {n}회" for w, n in ban.items())
                    + " → 뜻이 보이는 말로 풀어쓰기(칸→유형/구간, 값→금액/숫자, 자리→그 뜻대로)")
    for name, rx, th, fix in RULES:
        found = rx.findall(prose)
        # 횟수 + 밀도. 임계 1~2회 규칙은 60문장당 1회, 3회 규칙은 문장의 5% 이상일 때만
        rate = 0.05 if th >= 3 else 1 / 60
        if len(found) >= th and len(found) / len(sents) >= rate:
            ex = found[0] if isinstance(found[0], str) else found[0][0]
            hits.append(f"{name}: {len(found)}회 (예: '{ex.strip()}') → {fix}")
    # C-11(연결어미 뒤 쉼표)은 일부러 안 본다. 사람 글에서도 문장의 8~27%에 나와 AI 글(20%)과 구분이 안 된다.
    # 짧은 단정 연타(드라마 문형): 16자 이하 문장이 3개 연속
    run = 0; runs = []
    for s in sents:
        run = run + 1 if len(s.strip()) <= 16 else 0
        if run == 3:
            runs.append(s.strip())
    if len(runs) >= 2 and len(runs) / len(sents) >= 1 / 60:
        hits.append(f"짧은 단정 3연타(드라마 문형): {len(runs)}곳 (예 '{runs[0]}') → 앞 문장과 이어 한 문장으로")
    # 간접의문 소제목: 의문사 + '~나/~는가/~인가'로 끝나고 물음표 없이 콜론·따옴표·줄끝이 오는 제목
    ih = re.findall(r"(?:얼마나|무엇을|무엇이|뭘|왜|어떻게|어디서|어디에|누가|언제)[^\n?.!\"']{0,25}(?:나|는가|인가)(?=\s*(?:[:：\"'\n]|$))", text)
    if len(ih) >= 2:
        hits.append(f"간접의문 소제목: {len(ih)}회 (예: '{ih[0].strip()}') → 물음표 질문('얼마나 아낄 수 있을까?')이나 명사구로")
    # 정확히 3항목짜리 목록 덩어리가 연달아 나오는 틀
    blocks = [len(b.strip().splitlines()) for b in re.split(r"\n\s*\n", text)
              if b.strip() and all(re.match(r"\s*(?:[-*]|\d+[.)])\s+\S", ln) for ln in b.strip().splitlines())]
    if blocks.count(3) >= 5:  # 3덩어리로 두면 사람 문서에서 오탐이 난다
        hits.append(f"3항목 목록 강박: 정확히 3개짜리 목록이 {blocks.count(3)}덩어리 → 항목 수를 내용에 맞춰 다르게")
    nq = NOUN_Q_END.findall(text)
    if len(nq) >= 2:
        hits.append(f"의문형 명사 종결 드라마 문형('~인가.'): {len(nq)}회 → 물음표 질문으로 바꾸거나 '~인지 확인하세요'처럼 동사로")
    # 기준 표본 지문 대조(문장 길이 분포·쉼표·명령형). 세부는 style_profile.py
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import style_profile
        hits += style_profile.score(text)
    except Exception:
        pass
    # E-7 합쇼체·해요체 혼재
    # '~세요/~주세요'는 합쇼체 책에서도 쓰는 부드러운 명령형이라 해요체로 안 센다.
    hap = len(re.findall(r"니다[.!?]", prose)); hae = len(re.findall(r"(?<=[가-힣])(?<!세)요[.!?]", prose))
    if hap >= 3 and hae >= 3 and hae / (hap + hae) >= 0.08:  # 사람도 1~3%는 섞는다
        hits.append(f"E-7 합쇼체({hap})와 해요체({hae}) 섞임 → 한 가지로")
    return hits


def main() -> None:
    if os.environ.get("STYLE_GUARD") == "0":
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    tool, text, path = added_text(data)
    if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch", "Bash"):
        return
    if not text or "style-guard:off" in text or EXEMPT_PATH.search(path):
        return
    hits = check(text)
    if not hits:
        return
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{tool}\t{path}\t{len(hits)}건\t" + " | ".join(hits)[:400].replace("\n", " ") + "\n")
    except Exception:
        pass
    deny(
        "[글쓰기 문체 가드] 새로 넣는 한국어 글에 AI 티 문형이 있습니다. 고친 뒤 다시 쓰세요.\n"
        + "\n".join(f"- {h}" for h in hits)
        + f"\n\n기준: {AI_HOME / 'samples'} 의 사용자 원문 표본을 먼저 읽고 그 말투를 따르세요.\n"
        "사용자가 쓴 원문을 그대로 옮기는 중이라 오탐이면 본문에 style-guard:off 를 넣고 다시 보내세요."
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
