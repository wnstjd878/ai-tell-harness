# -*- coding: utf-8 -*-
# style-guard:off  (운영 메모와 알림 문구. 원고 아님)
"""
용도: AI 티 판정(ai_tell_judge.py)을 Claude Code / Codex 세션에 연결하는 끼어드는 스크립트 넷을 한 파일에 둔다.
  pre    도구 직전. 규칙 검사(writing_style_guard)와 같은 글을 받아
         - 규칙 검사에 막힌 글은 저장해 두고, 고친 글이 통과하면 짝으로 남긴다
         - 통과한 글이 12문장 이상이면 배경 판정을 건다(코드 파일과 명령 안의 글은 30문장 이상)
         - AI 가 덮어쓰기 전에, 사람이 그 파일을 고쳐 놨는지 본다
         - 명령 도구면 시작 시각을 적어 둔다
  post   도구 직후. 편집 도구면 그 파일의 지금 상태를 "AI 가 남긴 상태"로 적고,
         명령 도구면 그 명령이 도는 동안 바뀐 기록 파일을 전부 AI 상태로 적는다.
         이렇게 AI 가 손댄 순간만 표시해 두면, 그 밖의 변화는 사람이 고친 것이다.
         여기서는 아무것도 막지 않는다. 막는 건 규칙 검사와 아래 stop 이다.
  stop   답변 종료. 배경 판정이 끝나길 최대 45초 기다렸다가 C·D 등급이 있으면 답변을 막고 의심 문장을 돌려준다.
         같은 파일은 사용자 입력 한 번에 두 번까지만 막는다. 그 뒤는 다음 입력 때 알림으로 넘긴다.
         파일을 통째로 다시 써서 통과한 더 나중 판이 있으면 옛 판의 판정으로는 막지 않는다.
  prompt 입력 직후. 사람이 고친 파일을 찾아 기록하고, 사용자가 글 표현을 지적한 말이면 그 말과 직전 답변을 저장한다.
실행 시점: Claude Code settings.json 의 PreToolUse / PostToolUse / Stop / UserPromptSubmit. (등록 예시는 README)
입력: 끼어드는 스크립트 입력(stdin JSON).
출력: stop 만 {"decision":"block","reason":...}. 나머지는 조용하다.
규칙 검사와 따로 둔 까닭: Codex 는 등록한 스크립트 내용이 바뀌면 승인이 풀려 조용히 꺼진다.
      규칙 검사 파일은 되도록 안 건드리고, 같은 글을 여기서 따로 받아 그 파일의 check() 를 불러 쓴다.
끄는 법: AI_TELL_HOME 의 judge.enabled 를 지우면 배경 판정과 차단이 멈춘다(수정 기록 저장은 계속).
일부러 안 만든 것: 판정 실패 시 차단(ai_tell_judge.py 참조).
Codex: hooks.json 에 같은 넷을 등록하고 /hooks 화면에서 사람이 승인해야 돈다.
      승인 전에는 Codex 가 고친 파일이 사람이 고친 것으로 잡힌다(AI 가 손댄 표시가 안 남아서).
어떤 오류가 나도 세션을 끊지 않는다(exit 0).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import ai_tell_judge as J  # noqa: E402

TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch", "Bash")
CMD_TOOLS = ("Bash", "PowerShell", "shell_command")
CODE_EXT = re.compile(r"\.(py|ts|tsx|js|jsx|mjs|sh|ps1|json|sql|css|ya?ml|toml|vbs)$", re.I)
_HOME_RX = re.escape(str(J.ROOT).replace("\\", "/")).replace("/", r"[\\/]")
SKIP_PATH = re.compile(r"ai_tell_(judge|hooks|eval|calibrate)\.py$|style_learn\.py$|" + _HOME_RX + r"[\\/]", re.I)
CODE_MIN_SENT = 30
WAIT_SECONDS = 45
MAX_BLOCKS_PER_PATH = 2
# 사용자가 글 표현을 지적하는 말. 넓게 잡고, 지적이 아닌 것은 학습 단계의 모델이 버린다.
FEEDBACK_RX = re.compile(
    r"AI\s*(?:티|같|스럽|말투|투)|ai\s*(?:티|같|말투)|말투|워딩|문체|어색|번역투|이렇게 쓰지|라고 쓰지|표현이|표현을|"
    r"사람이 쓴|기계 같|딱딱|뭔\s*소리|무슨\s*말|이딴\s*(?:말|워딩|표현|문장)|문장이|단어 쓰지|쓰지\s*마")


def read_input() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def is_codex(data: dict) -> bool:
    return "/.codex/" in str(data.get("transcript_path") or "").replace("\\", "/")


# ── 도구 직전 ────────────────────────────────────────────────
PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add) File: (.+)$", re.M)


def _paths(data: dict) -> list:
    """이 도구 호출이 고치는 파일들. Codex 의 패치 도구는 경로가 패치 글 안에 들어 있다."""
    ti = data.get("tool_input") or data.get("toolInput") or {}
    out = []
    one = ti.get("file_path") or ti.get("path") or ti.get("notebook_path")
    if one:
        out.append(str(one))
    name = data.get("tool_name") or data.get("toolName") or ""
    if name == "apply_patch" or not one:
        for v in ti.values():
            if isinstance(v, str) and "*** " in v:
                out += [m.strip() for m in PATCH_FILE.findall(v)]
    cwd = str(data.get("cwd") or "")
    full = []
    for p in out:
        if cwd and not (os.path.isabs(p) or re.match(r"^[A-Za-z]:[\\/]", p)):
            p = os.path.join(cwd, p)
        if p not in full and not SKIP_PATH.search(p):
            full.append(p)
    return full


def _key(data: dict, session: str) -> str:
    return str(data.get("tool_use_id") or data.get("toolUseId") or session)


def pre(data: dict) -> None:
    import writing_style_guard as g
    session = str(data.get("session_id") or "nosession")
    name = data.get("tool_name") or data.get("toolName") or ""
    if name in CMD_TOOLS:
        J.cmd_begin(_key(data, session), session)   # 이 명령이 도는 동안 바뀐 파일은 AI 가 바꾼 것
    if name not in CMD_TOOLS:
        for p in _paths(data):
            J.check_human_edit(p)   # AI 가 덮어쓰기 전에 본다
            J.touch(session, p)
    tool, text, path = g.added_text(data)
    if tool not in TOOLS:
        return
    if not path and name == "apply_patch":
        path = (_paths(data) or [""])[0]
    if os.environ.get("STYLE_GUARD") == "0":
        return
    if not text or "style-guard:off" in text or g.EXEMPT_PATH.search(path) or SKIP_PATH.search(path):
        return
    n = sum(1 for _ in g.SENT.finditer(text))
    if n < g.MIN_SENTENCES:
        return
    hits = g.check(text)
    if hits:
        if not J.looks_secret(text):
            J.record_block(session, tool, path, text, hits)
        return
    J.on_pass(session, tool, path, text)
    if not J.ENABLED.exists() or J.looks_secret(text):
        return
    # 코드 파일과 명령 안의 글은 주석·안내 문구가 대부분이라 문턱을 높인다. 원고를 통째로 심은 경우만 걸린다.
    if n >= (CODE_MIN_SENT if (tool == "Bash" or CODE_EXT.search(path)) else J.JUDGE_MIN_SENT):
        J.enqueue(session, tool, path, text, n)


# ── 도구 직후 ────────────────────────────────────────────────
def post(data: dict) -> None:
    session = str(data.get("session_id") or "nosession")
    name = data.get("tool_name") or data.get("toolName") or ""
    if name in CMD_TOOLS:
        J.cmd_end(_key(data, session))
        return
    for p in _paths(data):
        J.after_edit(session, p)


# ── 답변 종료 ────────────────────────────────────────────────
def _superseded(job: dict, jobs: list) -> bool:
    """파일을 통째로 다시 쓴 더 나중 판이 통과했으면 옛 판의 판정은 버린다."""
    for _p, o in jobs:
        if (o.get("pathkey") == job.get("pathkey") and o.get("path") and o.get("tool") == "Write"
                and o.get("state") == "done" and o.get("t", 0) > job.get("t", 0)
                and o["verdict"]["grade"] not in J.BLOCK_GRADES):
            return True
    return False


def _bad_jobs(session: str) -> tuple[list, list]:
    jobs = J.session_jobs(session)
    bad = [(p, j) for p, j in jobs if j.get("state") == "done" and not j.get("delivered")
           and j["verdict"]["grade"] in J.BLOCK_GRADES]
    return [(p, j) for p, j in bad if not _superseded(j, jobs)], [(p, j) for p, j in bad if _superseded(j, jobs)]


def _lines(j: dict) -> list:
    v = j["verdict"]
    where = j["path"] or "(명령 안에 쓴 글)"
    out = [f"파일: {where}  등급 {v['grade']} (확실한 의심 {v['strong']}곳)"]
    if v.get("truncated"):
        out.append(f"(글이 길어 앞 {v.get('chars', 0):,}자만 읽었습니다)")
    return out + [f'- "{s["quote"]}" : {s["why"]} → {s["fix"]}' for s in v["suspects"] if s["level"] == "강"]


def stop(data: dict) -> None:
    session = str(data.get("session_id") or "nosession")
    deadline = time.time() + WAIT_SECONDS
    while True:
        jobs = J.session_jobs(session)
        if not any(j.get("state") == "pending" and time.time() - j.get("t", 0) < 600 for _p, j in jobs):
            break
        if time.time() > deadline:
            break
        time.sleep(1)
    for p, j in jobs:  # 10분 넘게 답이 없는 작업은 배경 프로세스가 죽은 것이다. 기록을 남긴다
        if j.get("state") == "pending" and time.time() - j.get("t", 0) >= 600:
            j.update(state="error", error="배경 판정이 끝나지 않음")
            J.write_json(p, j)
            with open(J.ERRORS, "a", encoding="utf-8") as f:
                f.write(f"{J.now()}\t{session}\t{j.get('path')}\t배경 판정이 끝나지 않음\n")
    J.snapshot_turn(session)
    if not J.ENABLED.exists():
        return
    bad, old = _bad_jobs(session)
    if not bad and not old:
        return
    turn_t = (J.read_json(J.TURNS / f"{J.safe_session(session)}.json") or {}).get("t", 0)
    counts_file = J.JOBS / J.safe_session(session) / "_blocks.count"
    counts = J.read_json(counts_file, {}) or {}
    if counts.get("_turn") != turn_t:
        counts = {"_turn": turn_t}  # 사용자가 새로 입력하면 횟수를 다시 센다
    lines, later = [], []
    for _p, j in bad:
        key = j["pathkey"] if j.get("path") else "bash:" + j["sha"]
        if counts.get(key, 0) >= MAX_BLOCKS_PER_PATH:
            later += _lines(j)
        else:
            counts[key] = counts.get(key, 0) + 1
            lines += _lines(j)
    if later:
        (J.NOTICE / f"{J.safe_session(session)}.txt").write_text(
            "[AI 티 판정] 이번 차례에 두 번 돌려보냈는데도 아래 문장이 남아 있습니다. 이번 답변에서 사용자에게 그대로 알리세요.\n"
            + "\n".join(later), encoding="utf-8")
    if lines:
        reason = (
            "[AI 티 판정] 이번에 쓴 글을 다시 읽어 보니 AI 가 쓴 티가 남아 있습니다. 아래 문장을 그 파일에서 고치세요.\n"
            + "\n".join(lines)
            + f"\n\n고칠 때는 {J.SAMPLES} 의 사용자 원문 한 구간을 먼저 읽고 그 말투를 따릅니다."
            f" 수정 기록에서 배운 AI 티는 {J.ROOT / 'learned_rules.md'} 에 있습니다."
            " 문장을 빼지 말고 고쳐 쓰세요."
            " 짚은 문장이 사용자가 쓴 원문이거나 판정이 틀렸다고 보면 고치지 말고, 답변에 그 이유를 한 줄 적으세요."
        )
        out = {"decision": "block", "reason": reason, "systemMessage": "AI 티 판정: 고칠 문장이 있어 답변을 돌려보냄"}
        sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
        sys.stdout.buffer.flush()
    # 내보낸 다음에 전달 표시를 찍는다. 먼저 찍으면 중간에 죽었을 때 아무도 못 본 채 소진된다
    counts_file.parent.mkdir(parents=True, exist_ok=True)
    counts_file.write_text(json.dumps(counts), encoding="utf-8")
    for p, j in bad + old:
        j["delivered"] = True
        J.write_json(p, j)


# ── 입력 직후 ────────────────────────────────────────────────
def prompt(data: dict) -> None:
    session = str(data.get("session_id") or "nosession")
    J._ensure()
    J.prune()
    J.close_cmds(session)          # 지난 차례에 끊긴 명령이 바꾼 파일은 AI 상태로 적는다
    J.sweep_human_edits()          # 지난 차례가 끝난 뒤 사람이 고친 파일. 이번 차례를 시작하기 전에 본다
    notice = ""
    nf = J.NOTICE / f"{J.safe_session(session)}.txt"
    if nf.exists():
        try:
            notice = nf.read_text(encoding="utf-8")
            nf.unlink()
        except Exception:
            pass
    if J.ENABLED.exists():
        # 지난 답변이 끝난 뒤에야 나온 판정(45초를 넘긴 것). 여기서 안 알리면 아무에게도 안 간다
        late, old = _bad_jobs(session)
        if late:
            notice += ("\n" if notice else "") + (
                "[AI 티 판정] 지난 차례에 쓴 글의 판정이 늦게 나왔습니다. 아래 문장이 AI 티로 잡혔으니 "
                "이번 답변에서 사용자에게 알리고, 사용자가 원하면 고치세요.\n" + "\n".join(x for _p, j in late for x in _lines(j)))
        for p, j in late + old:
            j["delivered"] = True
            J.write_json(p, j)
    J.turn_start(session)
    text = str(data.get("prompt") or "")
    if text and FEEDBACK_RX.search(text) and len(text) < 6000 and not J.looks_secret(text):
        prev = J.last_assistant_text(str(data.get("transcript_path") or ""))
        if J.looks_secret(prev):
            prev = ""
        J.append_jsonl(J.FEEDBACK, {"id": f"fb-{J.sha(session + text + str(time.time()))}", "ts": J.now(),
                                    "session": session, "cwd": str(data.get("cwd") or ""),
                                    "prompt": text[:3000], "prev": prev[:6000],
                                    "recent": J.recent_shas(session)})
    if notice:
        if is_codex(data):
            out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": notice}}
            sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
        else:
            sys.stdout.buffer.write((notice + "\n").encode("utf-8"))


def main() -> None:
    if os.environ.get("AI_TELL_CHILD") == "1":
        return  # 판정 호출이 띄운 세션에서는 아무것도 하지 않는다
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    data = read_input()
    if os.environ.get("AI_TELL_DUMP"):  # 도구가 넘기는 입력 모양을 볼 때만 켠다
        try:
            with open(os.path.join(os.environ["AI_TELL_DUMP"], f"dump-{mode}.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + chr(10))
        except Exception:
            pass
    try:
        (J.DATA / f".hooks-heartbeat-{mode}").write_text(J.now(), encoding="utf-8")
    except Exception:
        pass
    {"pre": pre, "post": post, "stop": stop, "prompt": prompt}.get(mode, lambda _d: None)(data)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        try:
            with open(J.ERRORS, "a", encoding="utf-8") as f:
                f.write(f"{J.now()}\thooks\t{sys.argv[1:]}\t{str(e)[:300]}\n")
        except Exception:
            pass
    sys.exit(0)
