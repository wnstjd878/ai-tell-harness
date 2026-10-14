"""
용도: AI 티 판정기가 AI 초안을 잘 잡는지, 글쓴이 문장을 잘못 짚는지 잰다. 글쓴이 문장은 창고에 넣어 다시 안 짚게 한다.
      기준 글은 AI_TELL_HOME/published/ 의 <번호>_draft.txt(AI 초안)와 <번호>_final.txt(글쓴이가 고쳐 발행한 글)다.
      발행본에서 짚인 문장 가운데 초안에 없던 문장은 글쓴이가 쓴 것으로 보고 잘못 짚은 것으로 센다.
실행 시점: 손으로. 판정 지시문, 문턱값, 배운 항목을 고친 뒤에 한 번.
      python ai_tell_calibrate.py measure [--ids 01,02] [--out 결과.json]
      python ai_tell_calibrate.py harvest [--ids 01,02]     (발행본의 글쓴이 문장을 창고에 넣는다)
      python ai_tell_calibrate.py add <초안 파일> <발행본 파일> <번호>     (새 쌍을 기준 글에 더한다)
입력: published/ 의 글, ai_tell_judge.judge
출력: 화면, 결과 JSON, AI_TELL_HOME/author_sentences.txt
외부 의존: claude CLI (판정 호출 한 편에 약 13,000토큰)
일부러 안 만든 것: 목록에 넣을 문장을 사람이 고르는 화면. 초안에 없던 문장은 전부 글쓴이 문장으로 본다.
              발행 전에 AI 에게 다시 시켜 나온 문장이 섞이면 틀린 가정이 되므로, 그런 문장은 author_sentences.txt 에서 그 줄을 지운다.
"""
from __future__ import annotations

import collections
import io
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_tell_judge as J  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def ids_all() -> list:
    return sorted({f.name.split("_")[0] for f in J.PUBLISHED.glob("*_final.txt")})


def rd(i: str, kind: str) -> str:
    return (J.PUBLISHED / f"{i}_{kind}.txt").read_text(encoding="utf-8")


def _one(job):
    i, kind = job
    try:
        v = J.judge(rd(i, kind), exclude="")
        return (i, kind), {k: v[k] for k in ("grade", "strong", "weak", "per1k", "chars", "suspects")}
    except Exception as e:  # noqa: BLE001
        return (i, kind), {"error": str(e)[:200]}


def measure(ids: list, out: str | None) -> int:
    jobs = [(i, k) for i in ids for k in ("draft", "final")]
    with ThreadPoolExecutor(6) as ex:
        got = dict(ex.map(_one, jobs))
    res, mine_total, kept_total, by_rule = {}, 0, 0, collections.Counter()
    bad = 0
    for i in ids:
        d, f = got[(i, "draft")], got[(i, "final")]
        if "error" in d or "error" in f:
            print(f"{i} 판정 실패: {d.get('error') or f.get('error')}")
            bad += 1
            continue
        mine = J.mine_flags(rd(i, "draft"), rd(i, "final"), f)
        kept = f["strong"] - len(mine)
        mine_total += len(mine)
        kept_total += kept
        by_rule.update(m["rule"] for m in mine)
        res[i] = {"draft": d, "final": f, "mine": mine}
        print(f"{i}  초안 {d['grade']} 확실 {d['strong']} ({d['per1k']})  |  발행본 {f['grade']} 확실 {f['strong']} ({f['per1k']})"
              f"  그중 글쓴이 문장 {len(mine)}")
    n = len(res)
    print(f"\n초안을 돌려보낸 것 {sum(1 for r in res.values() if r['draft']['grade'] in J.BLOCK_GRADES)}/{n}, "
          f"발행본을 돌려보낸 것 {sum(1 for r in res.values() if r['final']['grade'] in J.BLOCK_GRADES)}/{n}")
    print(f"발행본에서 잘못 짚은 글쓴이 문장 {mine_total}곳, 초안에서 남은 문장 {kept_total}곳")
    print("규칙별:", dict(by_rule.most_common()))
    if out:
        io.open(out, "w", encoding="utf-8").write(json.dumps(res, ensure_ascii=False, indent=1))
    return 1 if bad else 0


def harvest(ids: list) -> int:
    """발행본에서 글쓴이가 직접 쓴 문장을 창고(author_sentences.txt)에 넣는다."""
    n = 0
    for i in ids:
        n += J.add_author_sentences(J.author_written(rd(i, "draft"), rd(i, "final")))
    print(f"글쓴이 문장 {n}개를 창고에 더했습니다.")
    return 0


def main() -> int:
    a = sys.argv[1:]
    opt = lambda k: a[a.index(k) + 1] if k in a else None
    ids = opt("--ids").split(",") if opt("--ids") else None
    if a[:1] == ["measure"]:
        return measure(ids or ids_all(), opt("--out"))
    if a[:1] == ["harvest"]:
        return harvest(ids or ids_all())
    if a[:1] == ["add"] and len(a) == 4:
        J.PUBLISHED.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(a[1], J.PUBLISHED / f"{a[3]}_draft.txt")
        shutil.copyfile(a[2], J.PUBLISHED / f"{a[3]}_final.txt")
        print(f"{a[3]} 을 기준 글에 더했습니다. 지금 {len(ids_all())}쌍.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
