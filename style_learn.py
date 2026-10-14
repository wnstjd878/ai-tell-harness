# -*- coding: utf-8 -*-
# style-guard:off  (학습 지시문이 금지 문형을 예시로 담는다)
"""
용도: 수정 기록에서 "AI 가 쓴 글에만 나오는 문장 꼴"을 모은다. 글쓰기 규칙(어떻게 써야 좋은 글인가)은 모으지 않는다.
      처음 만들 때 "글쓴이가 어떻게 고쳤나"를 배우게 했더니 글 짓는 규칙만 잔뜩 나왔고, 그 규칙이 글쓴이 본인 글까지
      AI 티로 잡았다. 그래서 배우는 대상을 "AI 글에는 있고 내 글에는 없는 꼴"로 좁혔다.
      배운 항목은 AI_TELL_HOME/learned_rules.json 에 쌓이고, 판정기(ai_tell_judge.py)가 매번 읽어 지시문에 싣는다.
      사람이 읽는 판은 learned_rules.md 다.
실행 시점: 손으로 돌리거나, 하루 한 번 예약 실행(cron, launchd, 작업 스케줄러)에 건다.
입력: AI_TELL_HOME/data/pairs.jsonl 중 kind=human (AI 가 쓴 글을 사람이 직접 고친 것)
      AI_TELL_HOME/data/feedback.jsonl (사용자가 글 표현을 지적한 말 + 직전 답변)
      guard·judge 짝(AI 가 스스로 고친 것)은 배우지 않는다. 사람의 판단이 아니라서다. 나중에 모델 학습 재료로만 쌓는다.
출력: learned_rules.json / learned_rules.md, data/learn-runs.jsonl(전후 문장), data/rules-backups/
시험 두 번: 새 항목은 내 글 표본(samples/)에 돌려 걸리면 버리고, 서로 다른 AI 글 두 편 이상에서 안 걸려도 버린다.
      둘을 가르는 것만 남긴다. AI 글은 data/eval/ai_*.txt(ai_tell_eval.py --fresh 가 만든다), examples/ai_*.txt,
      수정 기록에 든 AI 원문에서 가져온다.
사용법:
  python style_learn.py              # 병합안만 보여준다. 저장 안 함
  python style_learn.py --go         # 저장
  python style_learn.py list         # 규칙 목록
  python style_learn.py off <번호>   # 사람이 끈다. 같은 취지의 규칙을 다시 만들지 않는다
  python style_learn.py on <번호>
  python style_learn.py rollback     # 직전 저장 전으로 되돌린다
  python style_learn.py compact [--go]   # 너무 길어진 항목을 한 줄로 줄인다
  python style_learn.py seed <AI글> <사람글> [메모]   # 수정 전후 파일 한 쌍을 손으로 넣는다
일부러 안 만든 것:
  - 규칙 검사(writing_style_guard.py)의 정규식 자동 수정. 정규식은 오탐 시험을 사람이 봐야 한다.
  - 실패 알림. 실패하면 종료 코드 1 과 data/learn.log 만 남긴다. 알림이 필요하면 예약 실행 쪽에서 종료 코드를 본다.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ai_tell_judge as J  # noqa: E402

if sys.stdout is None:  # 예약 실행(pythonw)은 출력 통로가 없다. 기록 파일로 돌린다.
    J._ensure()
    sys.stdout = sys.stderr = open(J.DATA / "learn.log", "a", encoding="utf-8", buffering=1)
    print(f"== {J.now()} 예약 실행 ==")

RUNS = J.DATA / "learn-runs.jsonl"
DONE = J.DATA / "learned_ids.json"
BACKUPS = J.DATA / "rules-backups"
RULES_MD = J.ROOT / "learned_rules.md"
HEARTBEAT = J.DATA / "learn.heartbeat"   # 마지막으로 성공한 시각(예약 실행이 살아 있는지 볼 때)
MAX_RULES = 40      # 판정 지시문에 다 실리므로 작게 둔다. 넘으면 비슷한 규칙에 합친다.
MAX_PER_RUN = 15    # 한 번에 배우는 기록 수. 남은 건 건수를 적고 다음 실행으로 넘긴다.
MAX_RULE_LEN = 140  # 규칙 한 줄 길이. 다듬을 때마다 예시가 붙어 문단이 되는 것을 막는다(처음엔 600자까지 불었다).
MAX_FAILS = 3       # 같은 기록이 이만큼 실패하면 건너뛴다. 안 그러면 실패하는 기록이 앞자리를 막는다.
FAILS = J.DATA / "learn_fails.json"
LOCK = J.DATA / "learn.lock"
CATEGORIES = "만든 말|경구·부풀림|틀 반복|자기 설명|비유|리듬|그 밖"

MERGE_PROMPT = """너는 AI 가 쓴 한국어 글에 남는 흔적(AI 티)을 모으는 분석가다.
관리하는 목록은 "AI 가 쓴 글에만 나오는 문장 꼴"이다. 이 목록은 글을 검사하는 판정기가 읽는다.

이 목록은 글쓰기 규칙이 아니다. 아래는 넣지 않는다.
- 어떻게 써야 좋은 글인가(구성, 도입과 마무리 방식, 문단 나누기, 소제목 꼴, 호칭, 사례 넣는 법)
- 글쓴이 개인의 취향과 말버릇, 어느 쪽으로 고치라는 처방
- 내용 선택(무엇을 넣고 뺐는지), 사실 고침

넣는 것은 기계가 문장을 만들 때 생기는 버릇뿐이다. 예를 들면 뜻 없이 멋만 낸 경구, 틀에 맞춘 되풀이,
사전에 없는 만든 말, 사실을 더하지 않는 부풀림, 글이 자기를 설명하는 문장, 일을 물건 다루듯 말하는 비유,
문단마다 똑같은 짜임. 판단 기준은 하나다. 사람이 직접 쓴 글에도 흔히 나오는 꼴이면 AI 티가 아니다.

아래는 (1) 지금까지 모은 AI 티와 (2) 이번 수정 기록 하나다. 증분 병합이다. 이번 기록에서 근거를 확인한 것만 건드려라.

[지금까지 모은 AI 티]
{current}

[사람이 직접 끈 것. 같은 취지를 다시 만들지 마라]
{banned}

[이미 다른 검사가 잡는 것. 다시 만들지 마라]
긴 줄표와 불릿 기호, "결론적으로/정리하자면", "다음과 같습니다", 이중 피동, 합쇼체와 해요체 섞임,
금지 단어(칸·값·묶음·쌍·시험대·자리·걸음·장치·축), 개수+명사 틀, 문서 자기 설명과 예고, 짧은 단정 연타,
영어 직역 명사로 닫는 문장, 기계 비유(묶다·붙이다·꼬리표), "핵심은/본질적으로" 같은 부풀림, 라벨과 설명 꼴 반복.

[이번 수정 기록]
{record}

손질 원칙:
- [AI 가 쓴 문장] 가운데 글쓴이가 지우거나 고친 것만 본다. 그 문장이 왜 기계가 쓴 것처럼 보이는지 꼴을 짚는다.
- [글쓴이가 쓴 문장]에 나오는 꼴은 AI 티가 아니다. 그 꼴을 잡는 항목은 만들지 마라. 기존 항목이 그 꼴을 잡고 있으면 drops.
- 글쓴이가 고친 까닭이 취향·내용·구성이면 아무것도 만들지 마라. 대부분의 수정은 여기에 해당한다. 억지로 뽑지 마라.
- 이번 기록이 글 표현에 관한 것이 아니면 세 배열을 다 비워라.
- 기존 항목과 같은 꼴이면 updates 로 더 정확하게 다듬는다. 지적 하나마다 항목을 늘리지 마라.
- 항목은 "무엇이 AI 티인가"만 한 줄, 100자 이내. 고치는 법은 적지 않는다. 특정 글의 소재·수치·문장을 넣지 마라. 예시는 꼴을 보여주는 낱말 둘까지.
- 총량은 {max_rules}개 이하. rule 앞에 [분류] 표시를 붙이지 마라.

출력은 JSON 만 (다른 말 금지):
{{"about_style": true 또는 false,
  "updates": [{{"id": 3, "rule": "한 줄", "evidence": "근거"}}],
  "adds": [{{"category": "{categories}", "rule": "한 줄", "evidence": "근거"}}],
  "drops": [{{"id": 4, "reason": "폐기 이유"}}]}}"""

GATE_PROMPT = """아래 [후보]만 기준으로 [글]에서 그 후보에 걸리는 문장을 찾아라.
걸리는 문장이 하나도 없을 수 있다. 억지로 찾지 마라. 후보의 꼴에 분명히 들어맞는 문장만 낸다.

[후보]
{rules}

[글]
{text}

출력 형식 (다른 말 없이 이것만):
{{"hits": [{{"key": "후보 앞에 적힌 표식 그대로(예: U3, N0)", "quote": "걸린 문장을 원문 그대로 60자 이내"}}]}}
걸리는 문장이 없으면 hits 를 빈 배열로 낸다."""


# ── 규칙 파일 ────────────────────────────────────────────────
def load() -> dict:
    d = J.read_json(J.LEARNED)
    if not isinstance(d, dict):
        d = {"version": 1, "next_id": 1, "rules": []}
    d.setdefault("rules", [])
    d.setdefault("next_id", 1 + max([r["id"] for r in d["rules"]] or [0]))
    return d


def save(d: dict) -> None:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    if J.LEARNED.exists():
        shutil.copy2(J.LEARNED, BACKUPS / f"learned_rules.{time.strftime('%Y%m%d-%H%M%S')}.json")
        for old in sorted(BACKUPS.glob("learned_rules.*.json"))[:-30]:
            old.unlink()
    d["updated_at"] = J.now()
    tmp = J.LEARNED.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    for i in range(8):
        try:
            os.replace(tmp, J.LEARNED)
            break
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    else:
        os.replace(tmp, J.LEARNED)
    render(d)
    for f in J.CACHE.glob("*.json"):  # 규칙이 바뀌면 옛 판정 결과는 못 쓴다
        try:
            f.unlink()
        except Exception:
            pass


def render(d: dict) -> None:
    act = [r for r in d["rules"] if r["status"] == "active"]
    lines = ["<!-- style-guard:off : 금지 문형을 예시로 담는다. 손으로 고치지 않는다. style_learn.py 가 만든다 -->",
             "# 수정 기록에서 모은 AI 티", "",
             f"사람이 직접 고친 글과 말로 한 지적에서, AI 가 쓴 글에만 나오는 문장 꼴만 뽑았다. 갱신 {d.get('updated_at', '')}, {len(act)}개.",
             "글쓰기 규칙이 아니다. 이 꼴이 글에 있으면 기계가 쓴 것처럼 보인다. 끄려면 `python style_learn.py off <번호>`.", ""]
    for r in act:
        lines.append(f"{r['id']}. [{r.get('category', '')}] {r['rule']}")
    RULES_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def active(d: dict) -> list:
    return [r for r in d["rules"] if r["status"] == "active"]


# ── 배울 기록 ────────────────────────────────────────────────
def diff_lines(before: str, after: str) -> tuple[list, list]:
    removed, added = [], []
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), n=0, lineterm=""):
        if line.startswith("-") and not line.startswith("---") and line[1:].strip():
            removed.append(line[1:].strip())
        elif line.startswith("+") and not line.startswith("+++") and line[1:].strip():
            added.append(line[1:].strip())
    return removed, added


def pending() -> list:
    done = set(J.read_json(DONE, []) or [])
    items = []
    for p in J.read_jsonl(J.PAIRS):
        if p.get("kind") == "human" and p["id"] not in done:
            items.append(("pair", p))
    for f in J.read_jsonl(J.FEEDBACK):
        if f["id"] not in done:
            items.append(("feedback", f))
    items.sort(key=lambda x: x[1].get("ts", ""))
    seen, out = set(), []
    for kind, it in items:
        if it["id"] not in seen:
            seen.add(it["id"])
            out.append((kind, it))
    return out


def lock() -> bool:
    """학습·끄기·되돌리기가 겹치면 서로 덮어쓴다. 한 번에 하나만 돈다."""
    J._ensure()
    try:
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime > 7200:
            LOCK.unlink()  # 죽은 실행이 남긴 것
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {J.now()}".encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def unlock() -> None:
    try:
        LOCK.unlink()
    except Exception:
        pass


def clean_rule(text: str) -> str:
    return re.sub(r"^(?:\s*\[[^\]\n]{1,12}\]\s*)+", "", str(text or "")).strip()


def as_int(v):
    try:
        return int(str(v).strip().lstrip("LlUu"))
    except Exception:
        return None


def mark(item_id: str) -> None:
    done = J.read_json(DONE, []) or []
    if item_id not in done:
        done.append(item_id)
        J.write_json(DONE, done)


def record_text(kind: str, it: dict) -> str | None:
    if kind == "pair":
        before, after = J.load_text(it["before"]), J.load_text(it["after"])
        if not before or not after:
            return None
        removed, added = diff_lines(before, after)
        if not removed and not added:
            return None
        cut = lambda xs: "\n".join("- " + x for x in xs[:120]) or "(없음)"
        return (f"종류: AI 가 쓴 글을 글쓴이가 직접 고쳤다.\n"
                f"[AI 가 쓴 문장 중 글쓴이가 지우거나 고친 것 {len(removed)}개]\n{cut(removed)}\n\n"
                f"[글쓴이가 쓴 문장 {len(added)}개 — 여기 나오는 꼴은 AI 티가 아니다]\n{cut(added)}")
    texts = "\n\n".join(f"[그 세션에서 AI 가 쓴 글 {i + 1}]\n{J.load_text(h)[:3000]}"
                        for i, h in enumerate(it.get("recent") or []) if J.load_text(h))
    return (f"종류: 글쓴이가 AI 에게 한 말이다. 글 표현에 대한 지적일 수도 있고 아닐 수도 있다.\n"
            f"[글쓴이의 말]\n{it.get('prompt', '')}\n\n[그 직전 AI 의 답변]\n{it.get('prev', '')[:4000] or '(없음)'}\n\n{texts}")


# ── 오탐 관문 ────────────────────────────────────────────────
def human_excerpts() -> str:
    """내 글 표본에서 뗀 토막(파일마다 최대 2,600자, 합쳐 13,000자 안팎). 긴 파일은 날마다 다른 자리를 뗀다."""
    parts, total = [], 0
    for f in J.sample_files():
        try:
            t = f.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        at = (int(time.strftime("%j")) * 9973) % max(len(t) - 2600, 1) if len(t) > 5200 else 0
        piece = t[at:at + 2600]
        parts.append(piece)
        total += len(piece)
        if total >= 13000:
            break
    return "\n\n".join(parts)


AI_MIN_TEXTS = 2   # 후보가 서로 다른 AI 글 이만큼에서 보여야 남긴다. 한 편에만 나오면 그 글의 우연이다.


def ai_texts() -> list:
    """AI 가 쓴 글 모음. 규칙 없이 쓰게 한 글(ai_tell_eval.py --fresh, examples/) + 수정 기록에 든 AI 원문(최근 20편)."""
    files = sorted((J.DATA / "eval").glob("ai_*.txt")) + sorted((Path(__file__).resolve().parent / "examples").glob("ai_*.txt"))
    out = [f.read_text(encoding="utf-8-sig")[:2500] for f in files]
    seen = set()
    for pr in reversed(J.read_jsonl(J.PAIRS)):
        if pr.get("kind") == "human" and pr["before"] not in seen and len(seen) < 20:
            seen.add(pr["before"])
            t = J.load_text(pr["before"])
            if J.count_sent(t) >= 12:
                out.append(t[:3000])
    return out


def _hits(cands: list, text: str):
    """후보별로 그 글에서 걸린 문장 모음({표식: [문장]}). 확인을 못 했으면 None."""
    keys = {key for key, _ in cands}
    rules = "\n".join(f"- {key}: {rule}" for key, rule in cands)
    esc = lambda s_: s_.replace("{", "{{").replace("}", "}}")
    try:
        out, _ = J.call_claude(GATE_PROMPT.format(rules=esc(rules), text=esc(text)), role="sonnet", timeout=240)
    except Exception as e:  # noqa: BLE001
        print(f"  시험 호출 실패: {e}")
        return None
    d = J.json_in(out)
    if not isinstance(d, dict) or not isinstance(d.get("hits"), list):
        print(f"  시험 답을 못 읽음: {str(out)[:100]}")
        return None
    flat = J._squash(text)
    found = {}
    for h in d["hits"]:
        m = re.search(r"([UN])\s*(\d+)", str(h.get("key") or ""))
        key = f"{m.group(1)}{m.group(2)}" if m else None
        if key not in keys:
            print(f"  시험이 모르는 표식을 돌려줌: {str(h.get('key'))[:30]}")
            return None
        q = J._squash(str(h.get("quote") or "")).strip("…\"'“”‘’ ")
        if q[:14] and q[:14] in flat:  # 글에 실제로 있는 문장만 친다
            found.setdefault(key, []).append(q[:14])
    return found


def gate(cands: list) -> tuple[dict, bool]:
    """후보마다 버릴 까닭을 돌려준다({표식: 까닭}, 확인이 됐는지). AI 티로 남기려면 둘 다 통과해야 한다.
       1) 사용자 원문에서 걸리지 않는다  2) 서로 다른 AI 글 두 편 이상에서 걸린다
    둘을 가르지 못하는 꼴은 AI 티가 아니라 글쓰기 취향이다.
    호출 실패, 답을 못 읽음, 모르는 표식, 빈 표본은 '확인 못 함'이다. 통과로 치지 않는다."""
    if not cands:
        return {}, True
    human, ais = human_excerpts(), ai_texts()
    if J.count_sent(human) < 30 or len(ais) < 4:
        print(f"  시험 글이 모자랍니다. 내 글 표본({J.SAMPLES})과 AI 글 넷 이상이 필요합니다. python ai_tell_eval.py --fresh 를 한 번 돌리면 AI 글이 생깁니다.")
        return {}, False
    in_human = _hits(cands, human)
    if in_human is None:
        return {}, False
    rest = [(k, r) for k, r in cands if k not in in_human]
    where = {k: set() for k, _ in rest}   # 후보가 걸린 AI 글 번호
    flats = [J._squash(t) for t in ais]
    batch, size = [], 0
    groups = []
    for i, t in enumerate(ais):          # 한 번에 9,000자 안팎씩 나눠 읽힌다
        if size + len(t) > 9000 and batch:
            groups.append(batch)
            batch, size = [], 0
        batch.append(i)
        size += len(t)
    if batch:
        groups.append(batch)
    for g in groups if rest else []:
        got = _hits(rest, "\n\n".join(ais[i] for i in g))
        if got is None:
            return {}, False
        for k, quotes in got.items():
            for q in quotes:
                where[k].update(i for i in g if q in flats[i])
    bad = {k: "사용자 원문에도 나옴" for k in in_human}
    bad.update({k: f"AI 글 {len(v)}편에서만 보임(기준 {AI_MIN_TEXTS}편)" for k, v in where.items() if len(v) < AI_MIN_TEXTS})
    return bad, True


# ── 병합 ─────────────────────────────────────────────────────
def merge_one(kind: str, it: dict, d: dict, go: bool) -> bool:
    rec = record_text(kind, it)
    if rec is None:
        print(f"  {it['id']} 바뀐 줄이 없어 넘어감")
        return True
    act = active(d)
    current = "\n".join(f"- (id={r['id']}) [{r.get('category', '')}] {r['rule']}" for r in act) or "(아직 없음. adds 로만 채워라.)"
    banned = "\n".join("- " + r["rule"] for r in d["rules"] if r["status"] == "off") or "(없음)"
    esc = lambda s: s.replace("{", "{{").replace("}", "}}")
    out, _ = J.call_claude(MERGE_PROMPT.format(current=esc(current), banned=esc(banned), record=esc(rec),
                                               max_rules=MAX_RULES, categories=CATEGORIES),
                           role="opus", timeout=300,
                           system="너는 글쓰기 규칙을 관리하는 분석가다. 요청한 JSON 만 출력한다.")
    ops = J.json_in(out)
    if not isinstance(ops, dict):
        print(f"  {it['id']} 병합 답을 못 읽음. 다음 실행에서 다시 봅니다.")
        return False
    ids = {r["id"] for r in act}
    updates, adds, drops, too_long = [], [], [], []
    for u in ops.get("updates") or []:
        u = dict(u, id=as_int(u.get("id")), rule=clean_rule(u.get("rule")))
        if u["id"] in ids and u["rule"]:
            (updates if len(u["rule"]) <= MAX_RULE_LEN else too_long).append(u)
    for a in ops.get("adds") or []:
        a = dict(a, rule=clean_rule(a.get("rule")))
        if a["rule"]:
            (adds if len(a["rule"]) <= MAX_RULE_LEN else too_long).append(a)
    for x in ops.get("drops") or []:
        x = dict(x, id=as_int(x.get("id")))
        if x["id"] in ids:
            drops.append(x)
    for r in too_long:
        print(f"  버림({MAX_RULE_LEN}자를 넘는 규칙): {r['rule'][:60]}…")
    label = "사람이 고친 글" if kind == "pair" else "말로 한 지적"
    if not (updates or adds or drops):
        print(f"  {it['id']} ({label}) 문체에 관한 수정이 아니거나 배울 게 없음")
        return True

    cands = [(f"U{u['id']}", u["rule"]) for u in updates] + [(f"N{i}", a["rule"]) for i, a in enumerate(adds)]
    bad, checked = gate(cands)
    if not checked:
        print(f"  {it['id']} 오탐 검사를 못 해 이번엔 배우지 않습니다. 다음 실행에서 다시 봅니다.")
        return False
    rejected = [f"{rule} ({bad[key]})" for key, rule in cands if key in bad]
    updates = [u for u in updates if f"U{u['id']}" not in bad]
    adds = [a for i, a in enumerate(adds) if f"N{i}" not in bad]

    print(f"\n{it['id']} ({label}) 병합안: 다듬기 {len(updates)} / 추가 {len(adds)} / 폐기 {len(drops)} / 시험에서 버림 {len(rejected)}")
    by = {r["id"]: r for r in d["rules"]}
    for u in updates:
        print(f"  다듬기 {u['id']}: {by[u['id']]['rule']}\n        → {u['rule']}")
    for a in adds:
        print(f"  추가 [{a.get('category', '')}] {a['rule']}")
    for x in drops:
        print(f"  폐기 {x['id']}: {by[x['id']]['rule']} ({x.get('reason', '')[:80]})")
    for r in rejected:
        print(f"  버림: {r}")
    if not go:
        return True

    fresh = load()
    live = {r["id"] for r in active(fresh)}
    updates = [u for u in updates if u["id"] in live]
    drops = [x for x in drops if x["id"] in live]
    d.clear()
    d.update(fresh)
    by = {r["id"]: r for r in d["rules"]}
    detail = {"ts": J.now(), "item": it["id"], "kind": kind, "rejected": rejected,
              "updates": [{"id": u["id"], "before": by[u["id"]]["rule"], "after": u["rule"],
                           "evidence": str(u.get("evidence") or "")[:400]} for u in updates],
              "adds": [], "drops": [{"id": x["id"], "rule": by[x["id"]]["rule"],
                                     "reason": str(x.get("reason") or "")[:300]} for x in drops]}
    for u in updates:
        by[u["id"]].update(rule=u["rule"], updated=J.now())
        if u.get("evidence"):
            by[u["id"]]["evidence"] = str(u["evidence"])[:400]
    for a in adds:
        rid = d["next_id"]
        d["next_id"] += 1
        d["rules"].append({"id": rid, "category": str(a.get("category") or "")[:20], "rule": a["rule"],
                           "evidence": str(a.get("evidence") or "")[:400], "status": "active",
                           "created": J.now(), "updated": J.now(), "source": it["id"]})
        detail["adds"].append({"id": rid, "rule": a["rule"]})
    for x in drops:
        by[x["id"]].update(status="dropped", updated=J.now(), off_reason=str(x.get("reason") or "")[:300])
    if updates or adds or drops:
        save(d)
    J.append_jsonl(RUNS, detail)
    n = len(active(d))
    if n > MAX_RULES:
        print(f"  주의: 규칙이 {n}개로 상한 {MAX_RULES}개를 넘었습니다. 다음 병합에서 합쳐집니다.")
    return True


COMPACT_PROMPT = """아래 항목들은 다듬을 때마다 예시가 붙어 너무 길어졌다. 각 항목을 100자 이내 한 줄로 줄여라.

줄이는 법:
- "무엇이 AI 티인가"만 적는다. 고치는 법은 적지 않는다.
- 나열된 예시 낱말은 대표 셋까지만 남기고 나머지는 그 꼴을 가리키는 넓은 말로 바꾼다.
- 한 규칙에 서로 다른 일이 둘 이상 들어 있으면 가장 자주 쓰일 하나만 남긴다. 뜻을 새로 보태지 마라.
- 앞의 [분류] 표시는 붙이지 마라.

[규칙]
{rules}

출력은 JSON 만:
{{"rules": [{{"id": 1, "rule": "줄인 한 줄"}}]}}"""


def compact(go: bool) -> int:
    """길이 상한을 넘는 규칙을 한 줄로 줄인다. 줄인 규칙도 오탐 관문을 다시 거친다."""
    d = load()
    for r in active(d):
        r["rule"] = clean_rule(r["rule"])
    long_ = [r for r in active(d) if len(r["rule"]) > MAX_RULE_LEN]
    if not long_:
        print("줄일 규칙이 없습니다.")
        if go:
            save(d)
        return 0
    esc = lambda s_: s_.replace("{", "{{").replace("}", "}}")
    out, _ = J.call_claude(COMPACT_PROMPT.format(rules=esc("\n".join(f"- (id={r['id']}) {r['rule']}" for r in long_))),
                           role="opus", timeout=300, system="너는 글쓰기 규칙을 관리하는 분석가다. 요청한 JSON 만 출력한다.")
    res = (J.json_in(out) or {}).get("rules")
    if not isinstance(res, list):
        print("줄인 답을 못 읽었습니다. 아무것도 바꾸지 않습니다.")
        return 1
    new = {as_int(x.get("id")): clean_rule(x.get("rule")) for x in res}
    cands = [(f"U{r['id']}", new[r["id"]]) for r in long_ if new.get(r["id"]) and len(new[r["id"]]) <= MAX_RULE_LEN]
    bad, checked = gate(cands)
    if not checked:
        print("오탐 검사를 못 해 아무것도 바꾸지 않습니다.")
        return 1
    detail = {"ts": J.now(), "item": "compact", "kind": "compact", "updates": [], "adds": [], "drops": [], "rejected": []}
    for r in long_:
        key, text = f"U{r['id']}", new.get(r["id"])
        if not text or len(text) > MAX_RULE_LEN:
            print(f"  {r['id']} 줄이지 못해 끕니다: {r['rule'][:50]}…")
            detail["drops"].append({"id": r["id"], "rule": r["rule"], "reason": "길이"})
            if go:
                r.update(status="dropped", updated=J.now(), off_reason="길이 상한을 넘고 줄이지 못함")
        elif key in bad:
            print(f"  {r['id']} 시험에서 떨어져 끕니다({bad[key]}): {text}")
            detail["drops"].append({"id": r["id"], "rule": r["rule"], "reason": bad[key]})
            if go:
                r.update(status="dropped", updated=J.now(), off_reason=bad[key])
        else:
            print(f"  {r['id']} {len(r['rule'])}자 → {len(text)}자: {text}")
            detail["updates"].append({"id": r["id"], "before": r["rule"], "after": text})
            if go:
                r.update(rule=text, updated=J.now())
    if go:
        save(d)
        J.append_jsonl(RUNS, detail)
        print(f"저장했습니다. 켜진 규칙 {len(active(d))}개.")
    else:
        print("저장하려면 compact --go")
    return 0


def run(go: bool) -> int:
    J._ensure()
    found = J.sweep_human_edits()
    if found:
        print(f"사람이 고친 파일 {found}개를 새로 찾았습니다.")
    items = pending()
    kept = 0
    for kind, it in items:   # 글쓴이가 직접 쓴 문장은 창고에 넣어 판정이 다시 짚지 않게 한다
        if kind == "pair" and go:
            b, a = J.load_text(it["before"]), J.load_text(it["after"])
            if b and a:
                kept += J.add_author_sentences(J.author_written(b, a))
    if kept:
        print(f"글쓴이가 직접 쓴 문장 {kept}개를 창고에 넣었습니다.")
    print(f"아직 안 배운 수정 기록 {len(items)}건")
    if not items:
        print("새로 배울 기록이 없습니다. (AI 가 쓴 글을 직접 고치거나 표현을 지적하면 쌓입니다)")
        return 0
    todo = items if go else items[:1]
    todo = todo[:MAX_PER_RUN]
    fail_counts = J.read_json(FAILS, {}) or {}
    fails = 0
    for kind, it in todo:
        d = load()
        try:
            ok = merge_one(kind, it, d, go)
        except Exception as e:  # noqa: BLE001
            print(f"  {it['id']} 병합 실패: {e}")
            ok = False
        if ok and go:
            mark(it["id"])
        elif not ok:
            fails += 1
            if go:
                fail_counts[it["id"]] = fail_counts.get(it["id"], 0) + 1
                if fail_counts[it["id"]] >= MAX_FAILS:
                    print(f"  {it['id']} {MAX_FAILS}번 실패해 건너뜁니다. 기록은 pairs.jsonl / feedback.jsonl 에 그대로 있습니다.")
                    mark(it["id"])
                J.write_json(FAILS, fail_counts)
    left = len(items) - len(todo)
    if not go:
        print(f"\n(미리보기는 첫 기록만. 나머지 {left}건은 --go 실행 때 차례로 병합합니다)\n저장하려면 --go 를 붙여 다시 실행하세요.")
        return 0
    if left:
        print(f"\n한 번에 {MAX_PER_RUN}건까지만 배웁니다. 남은 {left}건은 다음 실행에서 봅니다.")
    print(f"\n규칙 {len(active(load()))}개.")
    return 1 if fails else 0


def main() -> int:
    a = sys.argv[1:]
    if a and a[0] == "list":
        d = load()
        for r in d["rules"]:
            mark_ = {"active": " ", "off": "끔", "dropped": "폐기"}[r["status"]]
            print(f"{r['id']:3d} {mark_:2s} [{r.get('category', '')}] {r['rule']}")
        print(f"\n켜진 규칙 {len(active(d))}개 / 전체 {len(d['rules'])}개")
        return 0
    if a and a[0] in ("off", "on", "rollback", "compact"):
        if not lock():
            print("학습이 돌고 있습니다. 끝난 뒤(보통 몇 분) 다시 실행하세요.")
            return 2
        import atexit
        atexit.register(unlock)
    if a and a[0] == "compact":
        return compact("--go" in a)
    if a and a[0] in ("off", "on") and len(a) > 1:
        d = load()
        for r in d["rules"]:
            if r["id"] == int(a[1]):
                r.update(status="off" if a[0] == "off" else "active", updated=J.now())
                save(d)
                print(f"{r['id']}번 {'껐습니다' if a[0] == 'off' else '켰습니다'}: {r['rule']}")
                return 0
        print("그 번호의 규칙이 없습니다.")
        return 2
    if a and a[0] == "rollback":
        baks = sorted(BACKUPS.glob("learned_rules.*.json"))
        if not baks:
            print("되돌릴 저장본이 없습니다.")
            return 2
        shutil.copy2(baks[-1], J.LEARNED)
        baks[-1].unlink()
        render(load())
        print(f"{baks[-1].name} 상태로 되돌렸습니다.")
        return 0
    if a and a[0] == "seed" and len(a) > 2:
        J._ensure()
        before = Path(a[1]).read_text(encoding="utf-8-sig")
        after = Path(a[2]).read_text(encoding="utf-8-sig")
        J.add_pair("human", a[2], J.save_text(before), J.save_text(after),
                   note={"seed": a[3] if len(a) > 3 else "손으로 넣음"})
        print("수정 기록 한 쌍을 넣었습니다.")
        return 0
    go = "--go" in a
    if go and not lock():
        print("다른 학습이 돌고 있어 이번 실행은 건너뜁니다.")
        return 0
    try:
        rc = run(go)
    except Exception as e:  # noqa: BLE001
        print(f"학습 실패: {e}")
        return 1
    finally:
        if go:
            unlock()
    if go and rc == 0:
        HEARTBEAT.write_text(J.now(), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
