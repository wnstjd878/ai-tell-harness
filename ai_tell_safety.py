# -*- coding: utf-8 -*-
# style-guard:off  (검사 도구. 산문 없음)
"""
== 운영 맥락 ==
용도: AI 티를 고쳐 쓴 글을 받기 전에 원문을 망가뜨리지 않았는지 본다. 모델 호출 없음.
      ai_tell_judge.rewrite() 가 고친 문장마다 부른다. 고친 글을 자동으로 넣는 봇도 쓰기 직전에 부르면 된다.
      im-not-ai(MIT) 의 검사 스크립트를 그 저장소에서 바로 불러 쓴다(베껴 넣지 않는다. git pull 하면 따라온다).
        - strip_injected_commas.strip_injected : 고쳐 쓴 문장에 새로 생긴 연결어미 뒤 쉼표를 지운다
        - restore_modality.restore             : 사라진 당위·추측 표현("~해야 한다", "~일 것이다")이 있는 문장만 원문으로 되돌린다
        - metrics_v2.change_rate               : 글자 기준 변경률. 30% 이상 경고, 50% 이상 받지 않음
        - checks.run_checks                    : 숫자·8자 이상 인용·소제목·각주·해요체·격식 상향(하였) 보존
쓰는 법:
      import ai_tell_safety as S
      r = S.guard(before, after)              # 글 전체를 고친 경우
      r = S.guard(before, after, scope="sentence")   # 한 문장만 고친 경우(변경률은 보지 않음)
      r = S.guard(before, after, scope="redo")       # 일부러 통째로 다시 쓴 경우(명서당 장 다시 쓰기). 변경률·서법 되돌리기 안 함
      r["ok"] 이 False 면 고친 글을 버리고 원문을 쓴다. r["text"] 는 쉼표·서법을 손본 고친 글.
      단독 실행: python ai_tell_safety.py 원문.txt 고친글.txt
im-not-ai 위치: 환경변수 IM_NOT_AI, 없으면 ~/.ai-tell/im-not-ai, 그다음 ~/tools/ko-style/im-not-ai. 못 불러오면 숫자·변경률만 보는 자체 검사로 돈다(r["engine"]="fallback").
의도적 미구현: 의미 비교(모델 호출이 들어가 느리고 비싸다). 고친 뒤 다시 판정은 부르는 쪽이 정한다(ai_tell_pass 는 이미 다시 판정한다).
마지막 점검: 2026-10-21
"""
from __future__ import annotations

import difflib
import io
import json
import os
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

WARN = 0.30
ABORT = 0.50

_ENGINE: dict = {}


def _root() -> Path:
    if os.environ.get("IM_NOT_AI"):
        return Path(os.environ["IM_NOT_AI"])
    home = Path(os.environ.get("AI_TELL_HOME") or Path.home() / ".ai-tell").expanduser() / "im-not-ai"
    return home if home.exists() else Path.home() / "tools" / "ko-style" / "im-not-ai"


def _load() -> dict:
    """im-not-ai 검사 모듈을 한 번만 불러 둔다. 실패하면 빈 사전."""
    if "ok" in _ENGINE:
        return _ENGINE
    root = _root()
    for p in (root / "scripts", root / "skills" / "humanize-korean" / "references"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    try:
        with redirect_stdout(io.StringIO()):
            import checks  # type: ignore
            import metrics_v2  # type: ignore
            import restore_modality  # type: ignore
            import strip_injected_commas  # type: ignore
        _ENGINE.update(ok=True, checks=checks, m=metrics_v2, rm=restore_modality, sc=strip_injected_commas)
        global WARN, ABORT
        WARN, ABORT = metrics_v2.CHANGE_RATE_WARN, metrics_v2.CHANGE_RATE_ABORT
    except Exception as e:  # noqa: BLE001
        _ENGINE.update(ok=False, error=f"{type(e).__name__}: {e}"[:200])
    return _ENGINE


# ── 자체 검사(im-not-ai 를 못 불러왔을 때) ──────────────────
_NUM = re.compile(r"\d[\d,.]*\s*(?:%|퍼센트|원|만|억|천|배|명|개|건|곳|일|시간|분|초|년|월|위)?")


def _numbers(text: str) -> set:
    return {re.sub(r"[\s,]", "", m.group(0)) for m in _NUM.finditer(text)}


def _fallback_fails(before: str, after: str) -> list:
    lost = sorted(_numbers(before) - _numbers(after))
    return [f"[number_dropped] 원문 숫자가 사라짐: {', '.join(lost[:6])}"] if lost else []


def _rate(before: str, after: str) -> float:
    e = _load()
    if e.get("ok"):
        return e["m"].change_rate(before, after, ignore_markup=True)
    if not before and not after:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, before, after, autojunk=False).ratio()


def guard(before: str, after: str, scope: str = "doc") -> dict:
    """고친 글을 받아도 되는지. scope='doc' 글 전체, 'sentence' 문장 하나(변경률 안 봄), 'redo' 통째로 다시 쓴 글(변경률·서법 안 봄).
    돌려주는 것: ok(받아도 됨), text(쉼표·서법을 손본 고친 글), rate, fails(사유 목록), warn, fixes(손본 내역), engine."""
    before = before or ""
    after = after or ""
    e = _load()
    fixes: dict = {}
    text = after
    if not text.strip():
        return {"ok": False, "text": after, "rate": 1.0, "fails": ["[empty_output] 고친 글이 비어 있음"],
                "warn": [], "fixes": fixes, "engine": "im-not-ai" if e.get("ok") else "fallback"}
    if e.get("ok"):
        try:
            text, rep = e["sc"].strip_injected(before, text)
            if rep.get("commas_removed"):
                fixes["commas_removed"] = rep["commas_removed"]
        except Exception as ex:  # noqa: BLE001
            fixes["comma_error"] = str(ex)[:120]
        if scope != "redo":   # 통째로 다시 쓴 글은 문장 짝이 안 맞아 원문 문장을 끼워 넣으면 글이 섞인다
            try:
                with redirect_stdout(io.StringIO()):
                    text, restored, _skipped = e["rm"].restore(before, text)
                if restored:
                    fixes["modality_restored"] = len(restored)
            except Exception as ex:  # noqa: BLE001
                fixes["modality_error"] = str(ex)[:120]
        try:
            fails = [str(f) for f in e["checks"].run_checks(before, text)]
        except Exception as ex:  # noqa: BLE001
            fails = _fallback_fails(before, text)
            fixes["checks_error"] = str(ex)[:120]
    else:
        fails = _fallback_fails(before, text)
    # im-not-ai 는 사라진 숫자를 경고만 한다(문장을 합칠 때도 숫자가 빠질 수 있어서). 한 문장만 고칠 땐 합칠 일이 없으니 막는다.
    if scope == "sentence":
        lost = (e["checks"].dropped_numbers(before, text) if e.get("ok")
                else sorted(_numbers(before) - _numbers(text)))
        if lost:
            fails.append(f"[number_dropped] 원래 문장의 숫자가 사라짐: {lost[:6]}")
    warn = []
    rate = _rate(before, text)
    if scope == "doc":
        if rate >= ABORT:
            fails.append(f"[change_rate] 변경률 {rate:.0%} (받지 않는 선 {ABORT:.0%})")
        elif rate >= WARN:
            warn.append(f"[change_rate] 변경률 {rate:.0%} (경고 선 {WARN:.0%})")
    return {"ok": not fails, "text": text, "rate": round(rate, 3), "fails": fails, "warn": warn,
            "fixes": fixes, "engine": "im-not-ai" if e.get("ok") else "fallback"}


def guard_sentences(pairs: list) -> list:
    """[(before, after), ...] 문장 단위 검사. 각 결과에 guard(scope='sentence')."""
    return [guard(b, a, scope="sentence") for b, a in pairs]


def status() -> dict:
    e = _load()
    return {"engine": "im-not-ai" if e.get("ok") else "fallback", "root": str(_root()), "error": e.get("error", ""),
            "warn": WARN, "abort": ABORT}


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "status":
        print(json.dumps(status(), ensure_ascii=False))
    elif len(sys.argv) >= 3:
        b = Path(sys.argv[1]).read_text(encoding="utf-8-sig")
        a = Path(sys.argv[2]).read_text(encoding="utf-8-sig")
        r = guard(b, a, scope=sys.argv[3] if len(sys.argv) > 3 else "doc")
        r.pop("text")
        print(json.dumps(r, ensure_ascii=False, indent=1))
        sys.exit(0 if r["ok"] else 2)
    else:
        print(__doc__)
