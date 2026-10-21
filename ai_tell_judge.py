# -*- coding: utf-8 -*-
# style-guard:off  (판정 지시문이 금지 문형을 예시로 담는다)
"""
용도: 한국어 글의 AI 티를 LLM 이 읽고 판정하는 부품. 규칙 검사(writing_style_guard)가 통과시킨 글을
      한 번 더 읽어 등급(A~D)과 의심 문장을 낸다. 짚은 문장을 글쓴이 말투로 고쳐 쓴 안도 낸다(rewrite).
      수정 기록(막힌 글과 고친 글, AI 가 쓴 글과 사람이 고친 글, 사용자가 말로 한 지적)도 여기서 저장한다.
      그 기록은 style_learn.py 가 읽어 배운 항목(learned_rules.json)을 다듬고,
      이 부품은 판정할 때마다 그 항목을 지시문에 싣는다.
실행 시점: ai_tell_hooks.py 가 부른다(도구 직전·답변 종료·입력 직후). 손으로는 아래 사용법.
폴더: 환경변수 AI_TELL_HOME (기본 ~/.ai-tell) 아래에 전부 둔다.
      samples/              내가 직접 쓴 글 표본(.txt). 판정의 기준이다.
      data/                 판정 결과와 수정 기록
      learned_rules.json    수정 기록에서 배운 AI 티
      author_sentences.txt  내가 직접 쓴 문장 창고(한 줄 한 문장). 여기 있는 문장은 짚지 않는다
      profile.txt           (선택) 글쓴이와 독자를 한두 줄로
      keep.txt              (선택) 잡지 말아야 할 내 말버릇을 한 줄에 하나씩
      rules/quick-rules.md  (선택) 문법 층 규칙. im-not-ai(MIT)의 quick-rules.md 를 받아 둔다
      judge.enabled         이 파일이 있을 때만 배경 판정을 건다
출력: {"grade","reason","suspects":[{"level","rule","quote","why","fix"}]}
외부 의존: claude CLI (구독 로그인). 호출할 때 룰북과 기억 파일을 빼는 환경변수를 준다.
      안 주면 호출 한 번에 룰북이 통째로 실려 토큰이 수십 배 든다.
사용법:
  python ai_tell_judge.py init                # 폴더를 만들고 배경 판정을 켠다
  python ai_tell_judge.py judge 글.txt        # 그 자리에서 판정하고 사람이 읽는 요약을 낸다
  python ai_tell_judge.py judge 글.txt --json
  python ai_tell_judge.py judge 글.txt --rewrite   # 짚은 문장마다 고쳐 쓴 문장도 낸다
  python ai_tell_judge.py state               # 쌓인 기록 건수
  python ai_tell_judge.py bg <작업 파일>       # 배경 판정(끼어드는 스크립트가 띄운다)
일부러 안 만든 것:
  - 판정 실패 시 차단. CLI 한도나 로그인 문제로 글쓰기 전체가 멈추면 안 된다. 실패는 data/judge-errors.log 에 남긴다.
  - 따로 학습시킨 모델. 수정 전후 짝이 수백 건 쌓이기 전에는 재료가 없다. pairs.jsonl 이 그 재료다.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

NL = chr(10)
HOME = Path.home()
ROOT = Path(os.environ.get("AI_TELL_HOME") or HOME / ".ai-tell").expanduser()
DATA = Path(os.environ.get("AI_TELL_DATA") or ROOT / "data")  # 시험할 땐 환경변수로 딴 폴더를 준다
SAMPLES = ROOT / "samples"
TEXTS = DATA / "texts"          # 글 원문(지문 이름으로 저장)
JOBS = DATA / "jobs"            # 세션별 판정 작업
BLOCKS = DATA / "blocks"        # 규칙 검사에 막힌 글(고친 글이 오면 짝으로 묶는다)
TOUCH = DATA / "touch"          # 이번 차례에 AI 가 손댄 파일
TURNS = DATA / "turns"          # 세션별 차례 시작 시각(차단 횟수를 다시 세는 기준)
CMDS = DATA / "cmds"            # 지금 돌고 있는 AI 명령(그 사이 바뀐 파일은 AI 가 바꾼 것)
SNAP = DATA / "snapshots"       # AI 가 마지막으로 남긴 파일 상태
CACHE = DATA / "cache"          # 글 지문별 판정 결과
NOTICE = DATA / "notice"        # 다음 입력 때 띄울 알림
CWD = DATA / "cwd"              # 판정 호출을 띄우는 빈 폴더
VERDICTS = DATA / "verdicts.jsonl"
PAIRS = DATA / "pairs.jsonl"
FEEDBACK = DATA / "feedback.jsonl"
ERRORS = DATA / "judge-errors.log"
ENABLED = ROOT / "judge.enabled"
LEARNED = ROOT / "learned_rules.json"
AUTHOR_SENT = ROOT / "author_sentences.txt"   # 글쓴이가 직접 쓴 문장 창고(한 줄에 한 문장). 여기 있는 문장은 판정이 짚어도 버린다
PUBLISHED = ROOT / "published"        # 글쓴이가 고쳐서 발행한 글(<번호>_final.txt)과 그 AI 초안(<번호>_draft.txt)
AUTHOR_SENT_MAX = 20000
CHILD_SETTINGS = DATA / "child-settings.json"
QUICK_RULES = Path(os.environ.get("AI_TELL_RULES") or ROOT / "rules" / "quick-rules.md")
PROFILE_TXT = ROOT / "profile.txt"
KEEP_TXT = ROOT / "keep.txt"
MODELS = {"sonnet": os.environ.get("AI_TELL_MODEL_JUDGE") or "sonnet",     # 판정·시험
          "opus": os.environ.get("AI_TELL_MODEL_REWRITE") or "opus"}       # 고쳐 쓰기·학습

JUDGE_MIN_SENT = 12             # 이보다 짧은 글은 판정에 안 건다
CHUNK = 7000                    # 한 번에 읽히는 글자 수
MAX_CHUNKS = 3                  # 고쳐 쓰기에 앞뒤 맥락으로 싣는 조각 수
JUDGE_MAX_CHUNKS = 40           # 판정은 끝까지 읽는다(28만 자). 조각은 4개씩 동시에 부른다
JUDGE_WORKERS = 4
BLOCK_GRADES = ("C", "D")
PROSE_EXT = re.compile(r"\.(md|mdx|txt|html?)$", re.I)
SNAP_MAX_BYTES = 600_000
CMD_STALE = 1800                # 명령 하나가 이보다 오래 돌지는 않는다(도구 한도 10분)
KO_SENT = re.compile(r"[가-힣][^.!?\n]{5,}?(?:니다|세요|십시오|니까|[가-힣]다|[가-힣]요|죠)[.!?]")

SYSTEM = "너는 한국어 글의 AI 티를 판정하는 검사기다. 요청한 JSON 만 출력한다. 다른 말은 쓰지 않는다."

JUDGE_PROMPT = """아래 [글]에서 AI 가 쓴 티가 나는 문장을 찾아라. 고치지는 마라.

{profile}
[글쓴이 본인 글]은 글쓴이가 직접 쓴 글이고 AI 티가 없다. 이 글이 기준이다.
[글쓴이 본인 글]에 나오는 문장 꼴, 낱말, 말버릇은 [글]에 똑같이 나와도 잡지 마라. 글쓴이 습관이다.

[글쓴이 본인 글]
{ref}

[문법 층 규칙 (참고용. 위 본인 글에도 나오는 꼴이면 잡지 않는다)]
{rules}

[한 번만 나와도 '강'으로 칠 꼴 (im-not-ai 분류표의 S1. 글쓴이 습관과 겹치는 꼴은 s1_skip.txt 로 뺀다)]
{s1}

[이 글쓴이의 글에 나올 수 없는 표현]
1. 만든 말·추상 명사: 자리, 값, 변수, 묶음, 칸, 축, 쌍, 창, 걸음, 무기, 시험대, 장치를 뜻이 흐린 채로 쓴 곳. 사전에도 업계에도 없는 조어.
2. 개수+명사 틀: "두 질문", "세 길", "옵션 3종", "크게 세 가지로", "~는 세 가지뿐입니다".
3. 문서 자기 설명과 예고: "이 글은 ~를 다룬다", "뒤에서 다룬다".
4. 개념을 영어 직역 명사로 만들어 "~이다"로 닫은 문장("다시 세는 창이다"), 이중 피동.
5. 드라마 문형: 16자 안팎의 짧은 단정이 셋 넘게 연달아 나오는 곳, 뜻 없이 멋만 낸 경구("데이터는 고객이 남긴 흔적이다").
6. 기계 비유: 묶다·붙이다·꼬리표를 달다·짝을 맞추다처럼 일을 물건 다루듯 말한 곳.
7. 사실을 더하지 않는 부풀림: "결정적 전환점", "본질적으로", "핵심은", 출처 없는 "전문가들은".
8. 균일한 틀: 문단마다 같은 길이·같은 짜임, 항목마다 "라벨: 설명" 꼴 반복, 셋씩 맞춘 나열.
9. 앞 장 참조로 설명을 대신한 곳: "(PART 4 7장)", "PART 1 4장에서 다룬 대로", "앞에서 말씀드린 것처럼"처럼 장 번호나 앞 내용만 가리키고 넘어가 독자가 앞을 뒤져야 하는 문장.
   fix 는 "그 장의 요점을 한 구절로 다시 짚기". 앞으로 나올 내용을 안내하는 문장("PART 7에서 제공합니다")은 잡지 않는다.

[수정 기록에서 모은 AI 티 (글쓴이 원문에는 없고 AI 글에서만 확인된 꼴)]
{learned}

[잡지 않을 것]
{keep}
- 실제 수치·고유명사·인용문. 오타와 띄어쓰기.
- 코드, 설정, 명령, 표, 주석, 운영 메모, 로그 문구. 글이 코드나 스크립트면 그 안에 든 사람이 읽을 원고 문장만 본다.
  사람이 읽을 원고가 거의 없으면 suspects 를 빈 배열로 내고 reason 을 "원고 아님"으로 적는다.

[글]
{text}

출력 형식 (다른 말 없이 이것만):
{{"reason": "한 줄 근거",
  "suspects": [{{"level": "강 또는 약", "rule": "규칙 번호나 위 항목 번호(배운 규칙이면 L번호)", "quote": "의심 문장을 원문 그대로 60자 이내", "why": "왜 걸리는지 한 줄", "fix": "어느 쪽으로 고칠지 한 줄"}}]}}

level 기준: 강 = [글쓴이 본인 글] 같은 사람 글에는 거의 안 나오고 AI 글에서만 보이는 꼴. 약 = 사람도 가끔 쓰지만 어색한 꼴.
확신이 없으면 넣지 마라. 본인 글에 비슷한 문장이 하나라도 있으면 넣지 마라. 최대 10개.
quote 는 글에 있는 글자를 한 글자도 바꾸지 말고 옮겨라."""


# ── 파일 다루기 ──────────────────────────────────────────────
def _ensure() -> None:
    for d in (SAMPLES, TEXTS, JOBS, BLOCKS, TOUCH, TURNS, CMDS, SNAP, CACHE, NOTICE, CWD):
        d.mkdir(parents=True, exist_ok=True)
    if not CHILD_SETTINGS.exists():
        CHILD_SETTINGS.write_text('{"disableAllHooks": true}', encoding="utf-8")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def pathkey(path: str) -> str:
    p = (path or "").replace("\\", "/").lower().strip()
    return hashlib.sha1(p.encode("utf-8", "replace")).hexdigest()[:12] if p else "nopath"


def safe_session(session: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", session or "nosession")[:64]


def save_text(text: str) -> str:
    _ensure()
    h = sha(text)
    f = TEXTS / f"{h}.txt"
    if not f.exists():
        f.write_text(text, encoding="utf-8")
    return h


def load_text(h: str) -> str:
    try:
        return (TEXTS / f"{h}.txt").read_text(encoding="utf-8")
    except Exception:
        return ""


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    for i in range(8):  # 윈도우는 다른 프로세스가 읽는 중이면 바꿔치기가 거절된다
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    os.replace(tmp, path)


def append_jsonl(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    return out


def count_sent(text: str) -> int:
    return sum(1 for _ in KO_SENT.finditer(text or ""))


def change_rate(before: str, after: str) -> float:
    if not before:
        return 1.0
    if len(before) + len(after) > 40000:  # 긴 원고는 줄 단위로 끝까지 본다(앞만 보면 뒤쪽 수정을 놓친다)
        return 1.0 - difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False).ratio()
    return 1.0 - difflib.SequenceMatcher(None, before, after, autojunk=True).ratio()


SECRET_RX = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{20,}\.|Bearer\s+[A-Za-z0-9._-]{16,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY|[A-Z][A-Z0-9_]{2,}(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|_PW)\s*=\s*['\"]?[^\s'\"]{8,}")


def looks_secret(text: str) -> bool:
    """자격정보처럼 보이는 글자가 있으면 저장도 판정도 하지 않는다."""
    return bool(SECRET_RX.search(text or ""))


def strip_html(text: str) -> str:
    t = re.sub(r"<(script|style)[\s\S]*?</\1>", "", text)
    t = re.sub(r"<br\s*/?>|</(?:p|h[1-6]|li|div|tr|blockquote)>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", t.replace("&nbsp;", " ").replace("&amp;", "&")).strip()


# ── 규칙 읽기 ────────────────────────────────────────────────
def quick_rules() -> str:
    try:
        return QUICK_RULES.read_text(encoding="utf-8")
    except Exception:
        return ""


def learned_rules() -> list:
    d = read_json(LEARNED, {}) or {}
    return [r for r in (d.get("rules") or []) if r.get("status") == "active" and r.get("rule")]


def learned_block(rules: list | None = None) -> str:
    rules = learned_rules() if rules is None else rules
    if not rules:
        return "(아직 없음)"
    return "\n".join(f"- L{r['id']} [{r.get('category', '')}] {r['rule']}" for r in rules)


def _sentences(text: str) -> list:
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+|[\r\n]+", text) if len(_squash(x)) >= 8]


def _near_any(q: str, olds: list) -> bool:
    """q(공백 뺀 문장)가 olds 가운데 하나를 낱말 몇 개만 손본 문장인지."""
    import difflib
    for o in olds:
        sm = difflib.SequenceMatcher(None, q, o, autojunk=False)
        if sm.ratio() >= 0.55 or sm.find_longest_match(0, len(q), 0, len(o)).size >= 14:
            return True
    return False


def author_written(before: str, after: str) -> list:
    """고친 글(after)의 문장 가운데 고치기 전 글(before)에 닮은 문장이 없는 것. 글쓴이가 직접 쓴 문장으로 본다.
    AI 초안 문장을 낱말 몇 개만 바꾼 것은 글쓴이 문장으로 치지 않는다(이걸 안 가르면 오판 수를 두 배 넘게 잘못 센다)."""
    olds = [_squash(x) for x in _sentences(before)]
    return [s for s in _sentences(after) if not _near_any(_squash(s), olds)]


_AUTHOR_FLAT = {"mtime": None, "flat": ""}


def author_flat() -> str:
    """글쓴이가 직접 쓴 문장을 공백 없이 이어 붙인 것. 판정이 짚은 글귀가 여기 들어 있으면 버린다."""
    try:
        m = AUTHOR_SENT.stat().st_mtime
    except OSError:
        return ""
    if _AUTHOR_FLAT["mtime"] != m:
        _AUTHOR_FLAT["mtime"] = m
        _AUTHOR_FLAT["flat"] = "\x00".join(_squash(l) for l in AUTHOR_SENT.read_text(encoding="utf-8").splitlines() if l.strip())
    return _AUTHOR_FLAT["flat"]


def add_author_sentences(sents: list) -> int:
    """글쓴이 문장을 창고에 더한다. 더한 개수를 돌려준다. 상한을 넘으면 오래된 것부터 버린다."""
    try:
        have = [l for l in AUTHOR_SENT.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        have = []
    seen = {_squash(l) for l in have}
    new = []
    for s in sents:
        s = " ".join(str(s).split())
        q = _squash(s)
        if len(q) < 10 or q in seen or looks_secret(s) or not re.search(r"[가-힣]", s):
            continue
        seen.add(q)
        new.append(s[:400])
    if new:
        AUTHOR_SENT.parent.mkdir(parents=True, exist_ok=True)
        tmp = AUTHOR_SENT.with_suffix(".tmp")
        tmp.write_text("\n".join((have + new)[-AUTHOR_SENT_MAX:]) + "\n", encoding="utf-8")
        os.replace(tmp, AUTHOR_SENT)
    return len(new)


def is_author_sentence(quote: str) -> bool:
    q = _squash(quote)
    return len(q) >= 10 and q[:40] in author_flat()


def mine_flags(before: str, after: str, verdict: dict, only_strong: bool = True) -> list:
    """고친 글(after)에서 짚인 문장 가운데 글쓴이가 직접 쓴 것. 판정이 잘못 짚은 것으로 센다."""
    olds = [_squash(x) for x in _sentences(before)]
    flat = _squash(before)
    out = []
    for s in verdict.get("suspects") or []:
        if only_strong and s["level"] != "강":
            continue
        if _squash(s["quote"])[:25] in flat:
            continue   # AI 초안에서 그대로 남은 문장
        sp = sentence_span(after, s["quote"])
        full = after[sp[0]:sp[1]].strip() if sp else s["quote"]
        if _near_any(_squash(full), olds):
            continue   # AI 초안 문장을 조금 손본 것
        out.append({"quote": full, "rule": s["rule"], "why": s["why"]})
    return out


def rules_version() -> str:
    try:
        return sha(LEARNED.read_text(encoding="utf-8") + JUDGE_PROMPT)[:8]
    except Exception:
        return "none"


REF_CHARS = 2600   # 지시문에 싣는 본인 글 길이
NO_SAMPLE = "(표본 없음. 사람이 직접 쓴 평범한 한국어 글을 기준으로 삼아라.)"
DEFAULT_PROFILE = "글쓴이는 자기 일을 직접 글로 쓰는 사람이다."
DEFAULT_KEEP = '- 글쓴이 본인 글에 나오는 말버릇. 대조("A가 아니라 B"), "~것이다", "~때문이다" 같은 흔한 한국어 꼴은 그것만으로 잡지 않는다.'


def _is_polite(text: str) -> bool:
    return len(re.findall(r"니다[.!?]", text)) >= len(re.findall(r"[가-힣]다[.!?]", text)) * 0.5


def sample_files() -> list:
    return sorted(f for f in SAMPLES.glob("*.txt") if f.is_file())


def reference(text: str, exclude: str = "") -> str:
    """판정할 글의 말투(합쇼체/평어체)에 맞는 본인 글 한 토막. exclude 는 판정 대상과 같은 표본 파일 이름(시험할 때)."""
    want = _is_polite(text)
    same, other = [], []
    for f in sample_files():
        if exclude and f.name == exclude:
            continue
        try:
            t = f.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        if count_sent(t) < 5:
            continue
        (same if _is_polite(t) == want else other).append(t)
    for t in same + other:
        a = max(0, min(len(t) - REF_CHARS, len(t) // 3))   # 머리말을 피해 가운데쯤에서 뗀다
        return t[a:a + REF_CHARS]
    return NO_SAMPLE


def _lines_of(path: Path, default: str) -> str:
    try:
        t = path.read_text(encoding="utf-8-sig").strip()
    except Exception:
        t = ""
    return t or default


def keep_block() -> str:
    t = _lines_of(KEEP_TXT, "")
    if not t:
        return DEFAULT_KEEP
    return "\n".join("- " + l.strip().lstrip("-").strip() for l in t.splitlines() if l.strip())


TAXONOMY = Path(os.environ.get("AI_TELL_TAXONOMY") or QUICK_RULES.with_name("ai-tell-taxonomy.md"))
S1_SKIP_TXT = ROOT / "s1_skip.txt"
# im-not-ai 분류표의 S1(한 번만 나와도 AI) 가운데 기본으로 빼는 것.
#   A-16 대명사: im-not-ai 실측에서 사람이 2~3배 더 써서 그쪽도 빠른 규칙에서 뺐다.
# 내 글에 자주 나오는 S1 꼴(예: C-8 "A가 아니라 B" 대구)이 있으면 s1_skip.txt 에 번호를 한 줄씩 적는다.
S1_SKIP_DEFAULT = {"A-16"}
# 분류표 본문이 문턱을 따로 둔 항목. 사람 글도 연결어미 쉼표를 문장의 15% 정도 쓴다.
S1_NOTE = {"C-11": "한 글에 6번 넘게 몰려 있을 때만 강. 그 아래는 잡지 않는다"}
_S1_HEAD = re.compile(r"^###\s+([A-J]-\d+)\.\s+(.+?)\s+\[S1\]", re.M)
# quick_pattern 값 안에도 가운뎃점(·)이 있어 " · quick_fix:" 앞까지 통째로 읽는다(코덱스 검수: C-8 의 "2회+" 조건이 잘렸다)
_S1_QUICK = re.compile(r"quick_pattern:\s*(.+?)\s*·\s*quick_fix:")


def s1_skip() -> set:
    return S1_SKIP_DEFAULT | {ln.strip() for ln in _lines_of(S1_SKIP_TXT, "").splitlines() if ln.strip()}


def s1_block() -> str:
    """분류표에서 S1 항목을 그때그때 읽는다(rules/ai-tell-taxonomy.md). 없으면 이 칸은 비운다."""
    try:
        tx = TAXONOMY.read_text(encoding="utf-8")
    except Exception:
        return "(분류표 없음)"
    skip, lines = s1_skip(), []
    heads = list(_S1_HEAD.finditer(tx))
    for i, m in enumerate(heads):
        pid, title = m.group(1), m.group(2)
        if pid in skip:
            continue
        body = tx[m.end():heads[i + 1].start() if i + 1 < len(heads) else m.end() + 4000]
        q = _S1_QUICK.search(body)
        lines.append(f"- {pid} {title}" + (f" (기준: {q.group(1).strip()[:200]})" if q else "")
                     + (f" [{S1_NOTE[pid]}]" if pid in S1_NOTE else ""))
    return NL.join(lines) or "(없음)"


def build_prompt(text: str, learned: list | None = None, exclude: str = "") -> str:
    esc = lambda s: s.replace("{", "{{").replace("}", "}}")
    return JUDGE_PROMPT.format(profile=esc(_lines_of(PROFILE_TXT, DEFAULT_PROFILE)),
                               ref=esc(reference(text, exclude)), rules=esc(quick_rules() or "(규칙 파일 없음)"),
                               s1=esc(s1_block()), learned=esc(learned_block(learned)), keep=esc(keep_block()), text=esc(text))


# ── claude 호출 ──────────────────────────────────────────────
def _model(role: str) -> str:
    return MODELS.get(role, role)


def _config_dirs() -> list:
    """로그인 폴더 후보. 세션에서 불리면 그 세션의 폴더를 먼저 쓰고, 안 되면 기본 폴더로 한 번 더."""
    cur = os.environ.get("CLAUDE_CONFIG_DIR")
    return [cur, None] if cur else [None]


def _claude_exe() -> str:
    """예약 실행이나 ssh 에서는 PATH 에 없을 수 있어 자리를 차례로 본다."""
    for c in (shutil.which("claude"), HOME / ".local" / "bin" / "claude.exe", HOME / ".claude" / "local" / "claude",
              HOME / ".local" / "bin" / "claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if c and Path(c).exists():
            return str(c)
    return "claude"


def call_claude(prompt: str, role: str = "sonnet", timeout: int = 150, system: str | None = None) -> tuple[str, dict]:
    """글자를 돌려준다. 종료코드가 0 이어도 본문이 오류면 실패로 친다."""
    _ensure()
    exe = _claude_exe()
    args = [exe, "-p", "--model", _model(role), "--settings", str(CHILD_SETTINGS), "--tools", "",
            "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config",
            "--system-prompt", system or SYSTEM, "--output-format", "json"]
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # 창 없이
    last = "로그인 폴더 후보가 없음"
    for cfg in _config_dirs():
        env = dict(os.environ, AI_TELL_CHILD="1", STYLE_GUARD="0",
                   CLAUDE_CODE_DISABLE_CLAUDE_MDS="1", CLAUDE_CODE_DISABLE_AUTO_MEMORY="1")
        env.pop("CLAUDE_CODE_CHILD_SESSION", None)   # 다른 세션 안에서 불려도 독립된 호출로 뜬다
        if cfg:
            env["CLAUDE_CONFIG_DIR"] = cfg
        else:
            env.pop("CLAUDE_CONFIG_DIR", None)
        t0 = time.time()
        try:
            r = subprocess.run(args, input=prompt.encode("utf-8"), capture_output=True,
                               timeout=timeout, cwd=str(CWD), env=env, **kw)
        except subprocess.TimeoutExpired:
            last = f"{timeout}초 안에 답이 없음"
            continue
        raw = r.stdout.decode("utf-8", "replace").strip()
        try:
            d = json.loads(raw)
        except Exception:
            last = f"종료코드 {r.returncode}, 답을 못 읽음: {(raw or r.stderr.decode('utf-8', 'replace'))[:160]}"
            continue
        if r.returncode != 0 or d.get("is_error") or not isinstance(d.get("result"), str) or not d["result"].strip():
            last = f"종료코드 {r.returncode}, 본문 오류: {str(d.get('result'))[:160]}"
            continue
        usage = d.get("usage") or {}
        meta = {"ms": int((time.time() - t0) * 1000),
                "model": ", ".join((d.get("modelUsage") or {}).keys()),
                "in": (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
                      + (usage.get("cache_read_input_tokens") or 0),
                "out": usage.get("output_tokens") or 0}
        return d["result"], meta
    raise RuntimeError(last)


def json_in(out: str):
    m = re.search(r"\{.*\}", out or "", re.S)
    return json.loads(m.group(0)) if m else None


# ── 판정 ─────────────────────────────────────────────────────
def _chunks(text: str) -> tuple[list, bool]:
    if len(text) <= CHUNK:
        return [text], False
    parts, cur = [], ""
    for para in re.split(r"(\n\s*\n)", text):
        if len(cur) + len(para) > CHUNK and cur.strip():
            parts.append(cur)
            cur = ""
        while len(para) > CHUNK:
            parts.append(para[:CHUNK])
            para = para[CHUNK:]
        cur += para
    if cur.strip() or (cur and not parts):
        parts.append(cur)
    elif cur:
        parts[-1] += cur   # 끝에 남은 빈 줄은 앞 조각에 붙인다(버리면 원문과 글자 수가 어긋난다)
    # 나눈 조각을 이으면 원문과 글자 하나 다르지 않아야 한다(im-not-ai reassemble_chunks 와 같은 확인)
    if sha("".join(parts)) != sha(text):
        raise RuntimeError("글 나누기에서 글자가 빠짐")
    return parts[:JUDGE_MAX_CHUNKS], len(parts) > JUDGE_MAX_CHUNKS


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# 등급은 모델이 아니라 코드가 매긴다. 모델에게 등급을 맡기면 사람 글도 AI 글도 전부 B 가 나온다.
# 기준 = 글자 1,000자당 '강' 의심 문장 수. 문턱값은 시험 묶음(사람 글·AI 글)으로 정한다. ai_tell_eval.py 참조.
# 만든 사람의 측정: 사람 글 13토막은 0.00~0.44, 규칙 없이 쓴 AI 글 6편은 0.68~2.41.
# C 문턱 0.9 로 사람 글 오판 0/13, AI 글 5/6. 자기 표본으로 ai_tell_eval.py 를 돌려 보고 문턱을 맞춘다.
GRADE_CUTS = (("D", 2.0, 3), ("C", 0.9, 2), ("B", 0.4, 1))  # (등급, 1,000자당 강 의심 수, 최소 강 의심 수)


# 학습 규칙에 tail_only=true 가 붙은 것은 글 마지막 15% 안에서 짚힌 문장만 '강'으로 센다. 그 앞은 '약'으로 내려 보여 주기만 한다.
# 2026-10-21 첨삭: 경구(L28)는 사람도 AI 도 쓴다. AI 글은 강 경구 24개 중 18개가 글 마지막 15% 안(글을 닫는 한 줄),
# 사람 블로그는 14개 중 4개뿐(중간에 주장을 던지고 풀어 감). 이 기준으로 사람 오판 7/43 → 4/43, AI 적중 16 → 15/18(계산).
TAIL_START = 0.85


def tail_only_ids() -> set:
    return {f"L{r['id']}" for r in learned_rules() if r.get("tail_only")}


def _rel_pos(text: str, quote: str) -> float:
    flat = re.sub(r"\s+", "", text)
    k = re.sub(r"\s+", "", quote)[:25]
    i = flat.find(k) if k else -1
    return i / max(len(flat), 1) if i >= 0 else 1.0   # 못 찾으면 끝에 있다고 본다(덜 봐주는 쪽)


def _tail_only(text: str, suspects: list) -> None:
    ids = tail_only_ids()
    if not ids:
        return
    for s in suspects:
        if s["level"] == "강" and s["rule"].strip() in ids and _rel_pos(text, s["quote"]) < TAIL_START:
            s["level"] = "약"
            s["why"] = (s["why"] + " (글 중간 경구라 등급에는 세지 않음)")[:200]


def grade_of(strong: int, chars: int) -> tuple[str, float]:
    per = strong / max(chars / 1000.0, 1.0)
    for g, cut, need in GRADE_CUTS:
        if per >= cut and strong >= need:
            return g, round(per, 2)
    return "A", round(per, 2)


def judge(text: str, role: str = "sonnet", learned: list | None = None, timeout: int = 150, exclude: str = "") -> dict:
    """글을 읽고 의심 문장을 짚게 한 뒤 등급을 매긴다. 실패하면 예외."""
    parts, truncated = _chunks(text)
    flat = _squash(text)
    reasons, suspects, metas = [], [], []
    def _one(part):
        return call_claude(build_prompt(part, learned, exclude), role=role, timeout=timeout)
    if len(parts) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(min(JUDGE_WORKERS, len(parts))) as ex:
            answers = list(ex.map(_one, parts))
    else:
        answers = [_one(parts[0])]
    for out, meta in answers:
        d = json_in(out)
        if not d or not isinstance(d.get("suspects"), list):
            raise RuntimeError(f"판정 답을 못 읽음: {out[:160]}")
        reasons.append(str(d.get("reason") or ""))
        metas.append(meta)
        for s in d["suspects"]:
            q = _squash(str(s.get("quote") or ""))
            # 글에 실제로 있는 문장만 남긴다. 모델이 문장을 지어 짚으면 고칠 자리를 못 찾는다.
            if q and is_author_sentence(q):
                continue   # 글쓴이가 직접 쓴 문장으로 확인된 것은 짚지 않는다
            if q and (q in flat or q[:18] in flat):
                suspects.append({"level": "강" if "강" in str(s.get("level") or "") else "약",
                                 "rule": str(s.get("rule") or ""), "quote": q[:80],
                                 "why": str(s.get("why") or "")[:160], "fix": str(s.get("fix") or "")[:160]})
    _tail_only(text, suspects)
    suspects.sort(key=lambda s: s["level"] != "강")
    strong = sum(1 for s in suspects if s["level"] == "강")
    read_chars = sum(len(p) for p in parts)
    grade, per = grade_of(strong, read_chars)
    return {"grade": grade, "raw_grade": grade, "strong": strong, "weak": len(suspects) - strong, "per1k": per,
            "reason": " / ".join(r for r in reasons if r)[:300],
            "suspects": suspects[:max(14, 6 * len(parts))], "chunks": len(parts), "truncated": truncated, "chars": read_chars,
            "model": metas[0]["model"] if metas else "", "ms": sum(m["ms"] for m in metas),
            "tokens_in": sum(m["in"] for m in metas), "rules_ver": rules_version()}


# ── 고쳐 쓰기 ─────────────────────────────────────────────── (지시문이라 style-guard:off)
REWRITE_SYSTEM = "너는 한국어 글을 글쓴이의 말투로 고쳐 쓰는 편집자다. 요청한 JSON 만 출력한다."

REWRITE_PROMPT = """아래 [글]에서 번호가 붙은 문장만 고쳐 써라. 나머지 문장은 건드리지 않는다.

지킬 것
1. [글쓴이 원문]의 말투, 종결어미, 문장 길이를 따른다. 그 글에 없는 멋 부린 표현을 새로 만들지 않는다.
2. 글에 없는 사실, 숫자, 사례를 지어내지 않는다. 고칠 재료는 앞뒤 문장에 있는 내용뿐이다.
3. 각 문장의 [까닭]을 없애는 쪽으로만 고친다. 뜻은 그대로 둔다.
4. 문장을 빼지 않는다. after 는 반드시 채운다. 그 문장은 글쓴이가 하려던 말(주장, 결론, 당부)이다.
   글을 닫는 한 줄이나 경구 꼴 문장이면, 하려던 말은 그대로 두고 꼴만 바꾼다. 맞세우는 짜임("A가 아니라 B", "절반만 맞다")과
   넓게 일반화한 주어("현명한 사람은", "승자는", "앞서나가는 사람은")를 풀고, 이 글에서 실제로 다룬 대상과 내용으로 좁혀 평서문으로 쓴다.
   앞 문단에 근거가 된 사례나 숫자가 있으면 그것을 끌어와 한 문장에 담는다.
   글 전체나 한 꼭지를 닫는 마지막 한 줄이면 원문을 갈아 끼우지 않는다. merge 에 "함께"를 적고, after 에는 원문 바로 앞에 놓을
   구체 문장 하나만 쓴다(원문은 그 뒤에 그대로 남는다). 글쓴이는 닫는 문장을 지우거나 바꾸는 것보다 앞에 받쳐 주는 문장을 더하는 쪽을 고른다.
5. 앞 문장이나 뒤 문장과 이어 붙여야 풀리는 문제면 merge 에 "앞" 또는 "뒤"를 적고, after 에 이어 붙인 문장 전체를 쓴다.
6. 고친 문장이 다른 AI 티(대구, 경구, 개수 못 박기, 비유)를 새로 만들면 안 된다.
7. 앞 장을 가리키는 참조("(PART 4 7장)", "~장에서 다룬 대로")는 장 번호를 빼고 그곳에서 한 말을 한 구절로 다시 짚는다.
   그 말이 [글] 안에 없으면 지어내지 말고 after 에 원문을 그대로 쓴다.

[글쓴이 원문]
{ref}

[글]
{text}

[고칠 문장]
{targets}

JSON 한 덩어리만 출력한다.
{{"fixes": [{{"n": 1, "after": "고친 문장", "merge": ""}}]}}
"""

_SENT_END = re.compile(r"[.!?](?:[\"'”’)\]]+)?(?=\s|$)|\n")


def sentence_span(text: str, quote: str) -> tuple[int, int] | None:
    """짚은 글귀가 들어 있는 문장 전체의 자리. 못 찾으면 None."""
    pat = r"\s*".join(re.escape(c) for c in re.sub(r"\s+", "", quote)[:40])
    m = re.search(pat, text)
    if not m:
        return None
    a = 0
    for e in _SENT_END.finditer(text, 0, m.start()):
        a = e.end()
    e = _SENT_END.search(text, m.end() - 1)
    b = e.end() if e else len(text)
    while a < b and text[a] in " \t\r\n":
        a += 1
    return a, b


def _fill_missing(items: list, got: dict, text: str, role: str, timeout: int) -> dict:
    """고친 문장이 비어 온 것만 한 번 더 시킨다. 짚은 문장은 빼지 않고 고쳐서 내놓는다."""
    miss = [it for it in items if not str((got.get(it["n"]) or {}).get("after") or "").strip()]
    if not miss:
        return got
    targets = NL.join(f"{it['n']}. {it['before']}" + NL + f"   [까닭] {it['why']}" for it in miss)
    try:
        out, _m = call_claude(
            REWRITE_PROMPT.format(ref=reference(text), text=text[:CHUNK * MAX_CHUNKS], targets=targets)
            + NL + "위 문장들은 앞서 빈칸으로 돌아왔다. 이번에는 하나도 비우지 말고 전부 고친 문장을 채워라.",
            role=role, timeout=timeout, system=REWRITE_SYSTEM)
        d = json_in(out) or {}
        for f in d.get("fixes") or []:
            if isinstance(f, dict) and str(f.get("after") or "").strip():
                got[int(f.get("n") or 0)] = f
    except Exception:  # noqa: BLE001
        pass
    return got


def rewrite(text: str, verdict: dict, only_strong: bool = False, role: str = "opus", timeout: int = 240) -> list:
    """판정이 짚은 문장을 고쳐 쓴 안. [{n, level, rule, why, before, after, merge}] (문장을 빼는 안은 내지 않는다)."""
    items = []
    for s in verdict.get("suspects") or []:
        if only_strong and s["level"] != "강":
            continue
        sp = sentence_span(text, s["quote"])
        if not sp or any(sp[0] == it["span"][0] for it in items):
            continue
        items.append({"n": len(items) + 1, "level": s["level"], "rule": s["rule"], "why": s["why"],
                      "before": text[sp[0]:sp[1]].strip(), "span": sp})
    if not items:
        return []
    marked, pos = [], 0
    for it in sorted(items, key=lambda x: x["span"][0]):
        a, b = it["span"]
        marked.append(text[pos:a] + f"【{it['n']}】" + text[a:b])
        pos = b
    marked.append(text[pos:])
    targets = "\n".join(f"{it['n']}. {it['before']}\n   [까닭] {it['why']}" for it in items)
    out, _meta = call_claude(
        REWRITE_PROMPT.format(ref=reference(text), text="".join(marked)[:CHUNK * MAX_CHUNKS], targets=targets),
        role=role, timeout=timeout, system=REWRITE_SYSTEM)
    d = json_in(out)
    if not d or not isinstance(d.get("fixes"), list):
        raise RuntimeError(f"고쳐 쓴 답을 못 읽음: {out[:160]}")
    got = {int(f.get("n") or 0): f for f in d["fixes"] if isinstance(f, dict)}
    got = _fill_missing(items, got, text, role, timeout)
    for it in items:
        f = got.get(it["n"])
        it["after"] = None if f is None else (str(f.get("after") or "").strip() or None)
        it["merge"] = "" if f is None else str(f.get("merge") or "")
    _safety(items)
    return items


def _safety(items: list) -> None:
    """고친 문장마다 숫자·인용·서법·쉼표 검사(ai_tell_safety, im-not-ai 검사기). 고친 안은 지우지 않고
    safe=False 와 사유를 붙인다(사람에게 보여 줄 땐 같이 보이고, 자동으로 넣을 땐 safe 인 것만 넣는다)."""
    try:
        import ai_tell_safety as S
    except Exception:  # noqa: BLE001
        return
    for it in items:
        if not it.get("after"):
            continue
        r = S.guard(it["before"], it["after"], scope="sentence")
        it["after"] = r["text"]
        it["safe"] = r["ok"]
        if r["fails"]:
            it["safety"] = r["fails"]


def report(v: dict) -> str:
    lines = [f"AI 티 판정: {v.get('grade')}등급 (확실한 의심 {v.get('strong', 0)}곳, 1,000자당 {v.get('per1k', 0)}). {v.get('reason', '')}"]
    if v.get("truncated"):
        lines.append(f"(글이 길어 앞 {JUDGE_MAX_CHUNKS * CHUNK:,}자만 읽었습니다)")
    for s in v.get("suspects") or []:
        lines.append(f'- ({s["level"]}) [{s["rule"]}] "{s["quote"]}" : {s["why"]} → {s["fix"]}')
    return "\n".join(lines)


def report_fixes(fixes: list) -> str:
    lines = []
    for it in fixes:
        lines.append(f'{it["n"]}. ({it["level"]}) {it["before"]}')
        tag = {"앞": " (앞 문장과 이어 씀)", "뒤": " (뒤 문장과 이어 씀)", "함께": " (원문 앞에 더할 문장)"}.get(it.get("merge") or "", "")
        lines.append(f'   → {it.get("after") or "(고친 문장을 못 받음)"}{tag}')
        if it.get("safety"):
            lines.append(f'   (고친 글 검사에 걸림, 자동으로 넣지 마세요: {"; ".join(it["safety"])[:160]})')
    return "\n".join(lines)


# ── 배경 판정 작업 ───────────────────────────────────────────
def job_path(session: str, h: str) -> Path:
    return JOBS / safe_session(session) / f"{h}.json"


def _spawn(args: list) -> None:
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True, cwd=str(DATA), env=dict(os.environ, AI_TELL_CHILD="1"))
    if os.name == "nt":
        base = 0x00000008 | 0x00000200  # 부모와 떼어 띄움
        try:
            subprocess.Popen(args, creationflags=base | 0x01000000, **kw)
        except OSError:
            subprocess.Popen(args, creationflags=base, **kw)
    else:
        subprocess.Popen(args, start_new_session=True, **kw)


def enqueue(session: str, tool: str, path: str, text: str, n_sent: int) -> str:
    """글을 저장하고 배경 판정을 건다. 같은 글을 이미 판정했으면 그 결과를 쓴다."""
    _ensure()
    h = save_text(text)
    jp = job_path(session, h)
    old = read_json(jp)
    if old and not (old.get("state") == "pending" and time.time() - old.get("t", 0) > 600):
        return h  # 이미 걸었다. 10분 넘게 답이 없는 것만 다시 건다(배경 프로세스가 죽은 경우)
    job = {"id": f"{safe_session(session)}-{h}", "ts": now(), "t": time.time(), "session": session, "tool": tool,
           "path": path, "pathkey": pathkey(path) if path else "bash", "sha": h, "n_sent": n_sent,
           "state": "pending", "delivered": False, "paired": False}
    cached = read_json(CACHE / f"{h}.{rules_version()}.json")
    if cached:
        job.update(state="done", verdict=cached, cached=True)
        write_json(jp, job)
        return h
    write_json(jp, job)
    _spawn([sys.executable, str(Path(__file__).resolve()), "bg", str(jp)])
    return h


def run_job(jp: Path) -> None:
    job = read_json(jp)
    if not job or job.get("state") != "pending":
        return
    text = load_text(job["sha"])
    if re.search(r"\.html?$", job.get("path") or "", re.I):
        text = strip_html(text)  # 태그와 꾸밈 코드가 글자 수에 들어가면 등급이 낮게 나온다
    try:
        v = judge(text)
        job.update(state="done", verdict=v, done_ts=now())
        write_json(CACHE / f"{job['sha']}.{v['rules_ver']}.json", v)
        append_jsonl(VERDICTS, {"ts": now(), "session": job["session"], "path": job["path"], "tool": job["tool"],
                                "sha": job["sha"], "n_sent": job["n_sent"], "grade": v["grade"],
                                "raw_grade": v["raw_grade"], "reason": v["reason"], "suspects": v["suspects"],
                                "model": v["model"], "ms": v["ms"], "tokens_in": v["tokens_in"]})
    except Exception as e:  # noqa: BLE001
        job.update(state="error", error=str(e)[:300], done_ts=now())
        try:
            with open(ERRORS, "a", encoding="utf-8") as f:
                f.write(f"{now()}\t{job['session']}\t{job['path']}\t{str(e)[:300]}\n")
        except Exception:
            pass
    try:
        write_json(jp, job)
        if job["state"] == "done":
            _pair_judged(job)
    except Exception as e:  # noqa: BLE001
        try:
            with open(ERRORS, "a", encoding="utf-8") as f:
                f.write(f"{now()}\t{job['session']}\t{job['path']}\t결과 저장 실패: {str(e)[:200]}\n")
        except Exception:
            pass


def session_jobs(session: str) -> list:
    d = JOBS / safe_session(session)
    if not d.is_dir():
        return []
    return [(p, j) for p in sorted(d.glob("*.json")) if (j := read_json(p))]


def _pair_judged(job: dict) -> None:
    """같은 세션 같은 파일에서 C·D 를 받은 글 뒤에 더 나은 글이 오면 짝으로 남긴다."""
    if job["verdict"]["grade"] in BLOCK_GRADES:
        return
    for p, prev in session_jobs(job["session"]):
        if (prev.get("sha") != job["sha"] and prev.get("pathkey") == job["pathkey"] and prev.get("state") == "done"
                and not prev.get("paired") and prev.get("t", 0) < job.get("t", 0)
                and prev["verdict"]["grade"] in BLOCK_GRADES):
            if _related(load_text(prev["sha"]), load_text(job["sha"])):
                add_pair("judge", job["path"], prev["sha"], job["sha"],
                         note={"suspects": prev["verdict"]["suspects"], "from": prev["verdict"]["grade"],
                               "to": job["verdict"]["grade"]}, session=job["session"])
                prev["paired"] = True
                write_json(p, prev)


# ── 수정 기록(짝) ────────────────────────────────────────────
def _related(a: str, b: str) -> bool:
    """같은 글의 앞뒤 판인가. 전혀 다른 글을 짝으로 묶지 않으려는 검사."""
    if not a or not b:
        return False
    m = difflib.SequenceMatcher(None, a[:3000], b[:3000], autojunk=True)
    return m.quick_ratio() >= 0.45 and m.ratio() >= 0.30


def add_pair(kind: str, path: str, before: str, after: str, note=None, session: str = "") -> None:
    """kind: guard(규칙 검사에 막힌 글→고친 글) / judge(판정 C·D→고친 글) / human(AI 가 쓴 글→사람이 고친 글)"""
    append_jsonl(PAIRS, {"id": f"{kind}-{before}-{after}", "ts": now(), "kind": kind, "path": path,
                         "before": before, "after": after, "note": note, "session": session})


def record_block(session: str, tool: str, path: str, text: str, hits: list) -> None:
    _ensure()
    f = BLOCKS / safe_session(session) / f"{pathkey(path) if path else 'bash'}.json"
    old = read_json(f)
    if old and time.time() - old.get("t", 0) < 7200:
        return  # 가장 먼저 막힌 글(손대기 전 원문)을 남긴다
    write_json(f, {"t": time.time(), "ts": now(), "path": path, "tool": tool, "sha": save_text(text), "hits": hits})


def on_pass(session: str, tool: str, path: str, text: str) -> None:
    f = BLOCKS / safe_session(session) / f"{pathkey(path) if path else 'bash'}.json"
    old = read_json(f)
    if not old:
        return
    before = load_text(old["sha"])
    if _related(before, text):
        add_pair("guard", path, old["sha"], save_text(text), note={"hits": old.get("hits")}, session=session)
        try:
            f.unlink()
        except Exception:
            pass


# ── 차례와 파일 상태(사람이 고친 것 찾기) ────────────────────
def turn_start(session: str) -> None:
    _ensure()
    write_json(TURNS / f"{safe_session(session)}.json", {"t": time.time()})


# 누가 파일을 바꿨는지는 "AI 가 손댄 순간"만 표시해서 가른다.
#   편집 도구: 직전에 touch, 직후에 그 파일 상태를 적는다(snapshot).
#   명령 도구: 직전에 cmd_begin, 직후에 cmd_end 가 그 사이 바뀐 기록 파일을 전부 AI 상태로 적는다.
# "어느 세션이든 AI 차례가 진행 중이면 사람 수정으로 안 본다"로 짜면, 세션을 여러 개 띄워 두는
# 환경에서는 늘 누군가 진행 중이라 사람 수정을 하나도 못 잡는다.
def cmd_begin(key: str, session: str) -> None:
    _ensure()
    write_json(CMDS / f"{safe_session(key)}.json", {"t": time.time(), "session": session})


def absorb_since(t0: float) -> None:
    for sf in SNAP.glob("*.json"):
        snap = read_json(sf) or {}
        try:
            if snap.get("path") and Path(snap["path"]).stat().st_mtime >= t0 - 1:
                snapshot(snap["path"])
        except Exception:
            pass


def cmd_end(key: str) -> None:
    f = CMDS / f"{safe_session(key)}.json"
    d = read_json(f)
    if not d:
        return
    absorb_since(d.get("t", time.time()))
    try:
        f.unlink()
    except Exception:
        pass


def close_cmds(session: str) -> None:
    """끝맺음 없이 남은 이 세션의 명령 표시(Esc 로 끊긴 명령 등)를 닫는다."""
    for f in CMDS.glob("*.json"):
        if (read_json(f) or {}).get("session") == session:
            cmd_end(f.stem)


def _cmd_covers(mtime: float) -> bool:
    """그 시각에 돌고 있던 AI 명령이 있었나. 있었으면 사람이 고쳤다고 단정할 수 없다."""
    for f in CMDS.glob("*.json"):
        t = (read_json(f) or {}).get("t", 0)
        if time.time() - t < CMD_STALE and mtime >= t - 1:
            return True
    return False


def touch(session: str, path: str) -> None:
    if not path:
        return
    _ensure()
    write_json(TOUCH / safe_session(session) / f"{pathkey(path)}.json", {"t": time.time(), "path": path})


def _read_file(path: str) -> str | None:
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size > SNAP_MAX_BYTES:
            return None
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def snapshot(path: str) -> None:
    if not PROSE_EXT.search(path or ""):
        return
    text = _read_file(path)
    sf = SNAP / f"{pathkey(path)}.json"
    if text is None or count_sent(text) < JUDGE_MIN_SENT:
        try:
            sf.unlink()  # 옛 기록을 남기면 AI 가 줄인 글이 사람이 고친 것으로 잡힌다
        except Exception:
            pass
        return
    write_json(sf, {"path": path, "sha": save_text(text), "t": time.time(), "ts": now()})


def check_human_edit(path: str) -> bool:
    """AI 가 마지막으로 남긴 상태와 지금 파일이 다르고, 그 사이 AI 차례가 없었으면 사람이 고친 것으로 남긴다."""
    sf = SNAP / f"{pathkey(path)}.json"
    snap = read_json(sf)
    if not snap:
        return False
    text = _read_file(path)
    if text is None:
        return False
    h = sha(text)
    if h == snap["sha"]:
        return False
    try:
        mtime = Path(path).stat().st_mtime
    except Exception:
        return False
    if _cmd_covers(mtime):
        return False
    for tf in TOUCH.glob(f"*/{pathkey(path)}.json"):  # 끝맺음 없이 죽은 세션이 손댄 흔적
        if (read_json(tf) or {}).get("t", 0) > snap.get("t", 0):
            write_json(sf, {"path": path, "sha": save_text(text), "t": time.time(), "ts": now()})
            try:
                tf.unlink()
            except Exception:
                pass
            return False
    before = load_text(snap["sha"])
    rate = change_rate(before, text)
    write_json(sf, {"path": path, "sha": save_text(text), "t": time.time(), "ts": now()})
    if 0.003 <= rate <= 0.70 and count_sent(text) >= 5:
        add_pair("human", path, snap["sha"], h, note={"rate": round(rate, 4)})
        return True
    return False


def sweep_human_edits() -> int:
    n = 0
    for sf in SNAP.glob("*.json"):
        snap = read_json(sf) or {}
        path = snap.get("path")
        if not path:
            continue
        if time.time() - snap.get("t", 0) > 45 * 86400:
            try:
                sf.unlink()
            except Exception:
                pass
            continue
        try:
            if Path(path).stat().st_mtime <= snap.get("t", 0) + 1:
                continue
        except Exception:
            continue
        if check_human_edit(path):
            n += 1
    return n


def snapshot_turn(session: str) -> None:
    """차례가 끝날 때 남은 표시를 정리한다. 도구 직후 처리가 빠진 경우(막힌 호출, 끊긴 명령)를 여기서 닫는다."""
    close_cmds(session)
    d = TOUCH / safe_session(session)
    if d.is_dir():
        for tf in d.glob("*.json"):
            p = (read_json(tf) or {}).get("path")
            if p:
                snapshot(p)
            try:
                tf.unlink()
            except Exception:
                pass


def after_edit(session: str, path: str) -> None:
    """편집 도구가 끝난 직후. 그 파일의 지금 상태가 AI 가 남긴 상태다."""
    if not path:
        return
    snapshot(path)
    try:
        (TOUCH / safe_session(session) / f"{pathkey(path)}.json").unlink()
    except Exception:
        pass


def prune() -> None:
    """하루 한 번 묵은 기록을 치운다. 안 치우면 차례 파일이 세션마다 쌓여 입력 직후 검사가 느려진다."""
    stamp = DATA / ".pruned"
    try:
        if stamp.exists() and time.time() - stamp.stat().st_mtime < 86400:
            return
        stamp.write_text(now(), encoding="utf-8")
        cut = {TURNS: 2, TOUCH: 2, CMDS: 1, BLOCKS: 2, NOTICE: 7, JOBS: 21, CACHE: 21}
        for base, days in cut.items():
            for f in base.rglob("*"):
                if f.is_file() and time.time() - f.stat().st_mtime > days * 86400:
                    f.unlink()
            for d in sorted((x for x in base.rglob("*") if x.is_dir()), reverse=True):
                if not any(d.iterdir()):
                    d.rmdir()
    except Exception:
        pass


def recent_shas(session: str, n: int = 3) -> list:
    jobs = sorted((j for _p, j in session_jobs(session)), key=lambda j: j.get("t", 0), reverse=True)
    return [j["sha"] for j in jobs[:n]]


def last_assistant_text(transcript_path: str) -> str:
    """대화 기록 파일(jsonl)에서 마지막 AI 답변의 글자만 꺼낸다. 못 읽으면 빈 글."""
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    for line in reversed(lines[-400:]):
        try:
            d = json.loads(line)
        except Exception:
            continue
        msg = d.get("message") or d.get("payload") or {}
        if (d.get("type") or msg.get("type")) not in ("assistant", "message") or msg.get("role") != "assistant":
            continue
        c = msg.get("content")
        if isinstance(c, str):
            return c
        texts = [x.get("text", "") for x in c or [] if isinstance(x, dict) and x.get("type") in ("text", "output_text")]
        if any(texts):
            return "\n".join(texts)
    return ""


SAMPLES_GUIDE = """여기에 내가 직접 쓴 글을 .txt 로 넣는다 (UTF-8).

- AI 가 한 글자도 안 쓴 글만 넣는다. AI 초안을 고친 글은 넣지 않는다.
- 한 파일에 2,000자 이상. 서너 편이면 시작할 수 있고, 많을수록 판정이 정확해진다.
- 말투가 둘이면(합쇼체 "~습니다", 평어체 "~다") 각각 한 편 이상 넣는다. 판정할 글의 말투에 맞는 표본이 기준이 된다.
- 넣은 뒤 `python style_profile.py build <이름> <파일...>` 로 문장 길이 기준을 만들고,
  `python ai_tell_eval.py` 로 내 글이 AI 로 잘못 잡히지 않는지 본다.
"""


def init() -> None:
    _ensure()
    guide = SAMPLES / "README.md"
    if not guide.exists():
        guide.write_text(SAMPLES_GUIDE, encoding="utf-8")
    if not ENABLED.exists():
        ENABLED.write_text("", encoding="utf-8")
    print(f"폴더: {ROOT}")
    print(f"표본 {len(sample_files())}편 ({SAMPLES}), 배경 판정 켜짐")
    if not QUICK_RULES.exists():
        print(f"문법 층 규칙 파일이 없습니다(없어도 돕니다): {QUICK_RULES}")


# ── 손으로 부르기 ────────────────────────────────────────────
def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "bg" and len(sys.argv) > 2:
        run_job(Path(sys.argv[2]))
        return 0
    if cmd == "judge" and len(sys.argv) > 2:
        src = sys.argv[2]
        text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8-sig")
        if re.search(r"\.html?$", src, re.I):
            text = strip_html(text)
        if not sample_files():
            print(f"(표본이 없습니다. {SAMPLES} 에 내가 쓴 글을 넣으면 그 글이 기준이 됩니다)", file=sys.stderr)
        v = judge(text)
        if "--rewrite" in sys.argv:
            v["fixes"] = [{k: it.get(k) for k in ("n", "level", "rule", "why", "before", "after", "merge", "safe", "safety")}
                          for it in rewrite(text, v)]
        if "--json" in sys.argv:
            print(json.dumps(v, ensure_ascii=False, indent=1))
        else:
            print(report(v))
            if v.get("fixes"):
                print("\n고쳐 쓴 문장\n" + report_fixes(v["fixes"]))
            print(f"\n({v['ms'] / 1000:.1f}초, 입력 {v['tokens_in']:,}토큰, 모델 {v['model']})")
        return 0 if v["grade"] not in BLOCK_GRADES else 3
    if cmd == "init":
        init()
        return 0
    if cmd == "state":
        _ensure()
        pairs = read_jsonl(PAIRS)
        kinds = {}
        for p in pairs:
            kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
        vs = read_jsonl(VERDICTS)
        g = {}
        for v in vs:
            g[v["grade"]] = g.get(v["grade"], 0) + 1
        print(f"배경 판정: {'켜짐' if ENABLED.exists() else '꺼짐'}")
        print(f"판정 {len(vs)}건 {dict(sorted(g.items()))}")
        print(f"수정 기록 {len(pairs)}건 {kinds}")
        print(f"말로 한 지적 {len(read_jsonl(FEEDBACK))}건")
        print(f"배운 규칙 {len(learned_rules())}개, 파일 상태 기록 {len(list(SNAP.glob('*.json')))}개")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
