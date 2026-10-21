# style-guard:off  모델을 부르지 않는 부분만 시험한다(claude CLI 없이 돈다)
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ai_tell_judge as J
import ai_tell_safety as S
import style_profile as sp

IM = Path(os.environ.get("IM_NOT_AI") or Path.home() / "tools" / "ko-style" / "im-not-ai")
HAS_IM = (IM / "scripts" / "checks.py").exists()
DOC = "## 매출\n\n지난달 매출은 1,200만원이었다. 광고비를 30% 줄여야 한다.\n"


def test_grade_cuts():
    assert J.grade_of(0, 3000)[0] == "A"
    assert J.grade_of(1, 1000)[0] == "B"
    assert J.grade_of(3, 3000)[0] == "C"
    assert J.grade_of(6, 2000)[0] == "D"
    assert J.grade_of(2, 300)[0] == "C"   # 1,000자 안 되는 글은 1,000자로 친다


def test_chunks_cover_whole_text():
    text = "가나다라마바사 아자차카타파하입니다.\n\n" * 6000
    parts, truncated = J._chunks(text)
    assert "".join(parts) == text and not truncated
    assert all(len(p) <= J.CHUNK for p in parts)


def test_style_points_group_short_sentences():
    hits = ["지문 short16=30 (...)", "지문 short25=60 (...)"]
    assert sp.points(hits) == 1
    assert sp.points(hits + ["지문 comma=0.05 (...)"]) == 2


@pytest.mark.skipif(not HAS_IM, reason="im-not-ai 가 없음")
def test_safety_with_im_not_ai():
    assert S.status()["engine"] == "im-not-ai"
    assert S.guard(DOC, DOC)["ok"]
    assert not S.guard(DOC, DOC.replace("1,200만원", "1,000만원"))["ok"]   # 숫자 바뀜
    assert not S.guard(DOC, DOC.replace("## 매출", "매출"))["ok"]          # 소제목 사라짐
    assert not S.guard(DOC, "")["ok"]
    assert not S.guard("하루 3잔이면 충분합니다.", "하루 세 잔이면 넉넉합니다.", scope="sentence")["ok"]


def test_safety_fallback_without_im_not_ai(tmp_path):
    code = ("import ai_tell_safety as S; r = S.guard('매출은 1,200만원이었다.', '매출은 1,000만원이었다.', scope='sentence');"
            "print(S.status()['engine'], r['ok'])")
    env = dict(os.environ, IM_NOT_AI=str(tmp_path / "none"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         cwd=str(Path(__file__).resolve().parent.parent)).stdout.strip()
    assert out == "fallback False"


@pytest.mark.skipif(not HAS_IM, reason="im-not-ai 가 없음")
def test_s1_block_reads_taxonomy(monkeypatch):
    monkeypatch.setattr(J, "TAXONOMY", IM / "skills" / "humanize-korean" / "references" / "ai-tell-taxonomy.md")
    block = J.s1_block()
    assert "A-8" in block and "- A-16 " not in block
    assert "6번 넘게" in block   # C-11 문턱 설명


def test_prompt_has_all_slots():
    p = J.build_prompt("시험 글입니다.")
    assert "시험 글입니다." in p and "한 번만 나와도" in p


# ── 코덱스 검수(2026-10-21)에서 나온 경우 ──
def test_chunks_keep_trailing_blank_lines():
    text = "가" * 7000 + "\n\n"
    parts, _ = J._chunks(text)
    assert "".join(parts) == text


def test_safety_fallback_rejects_new_number(tmp_path):
    code = ("import ai_tell_safety as S; r = S.guard('광고비를 줄였습니다.', '광고비를 30% 줄였습니다.', scope='sentence');"
            "print(r['ok'])")
    env = dict(os.environ, IM_NOT_AI=str(tmp_path / "none"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         cwd=str(Path(__file__).resolve().parent.parent)).stdout.strip()
    assert out == "False"


@pytest.mark.skipif(not HAS_IM, reason="im-not-ai 가 없음")
def test_safety_rejects_unrestored_modality():
    r = S.guard("광고비를 줄이려면 먼저 소재부터 바꿔야 한다.", "소재를 먼저 손보면 광고비가 준다.", scope="sentence")
    assert not r["ok"] and any("modality_lost" in f for f in r["fails"])


@pytest.mark.skipif(not HAS_IM, reason="im-not-ai 가 없음")
def test_s1_keeps_repeat_condition(monkeypatch):
    monkeypatch.setattr(J, "TAXONOMY", IM / "skills" / "humanize-korean" / "references" / "ai-tell-taxonomy.md")
    c8 = [ln for ln in J.s1_block().splitlines() if ln.startswith("- C-8 ")]
    assert c8 and "2회+" in c8[0]
