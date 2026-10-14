# ai-tell-harness

AI 가 쓴 한국어 글에서 기계가 쓴 티가 나는 문장을 짚고, 내 말투로 고친 문장까지 내놓은 뒤 그 글을 쓴 AI 에게 돌려보내 고치게 하는 도구입니다.

전자책 『AI 완벽 가이드』 부록 X 에서 설명하는 도구입니다.

Claude Code 나 Codex 가 파일에 글을 쓰는 순간 끼어들어 돌아가고, 손으로 글 한 편을 넣어 판정만 받아 볼 수도 있습니다. 판정은 claude CLI 를 구독 로그인으로 불러서 하므로 따로 조회 호출 열쇠를 만들 필요가 없습니다.

## 원리

글이 파일에 들어가기까지 다섯 단계를 거치고, 사람이 고친 기록이 여섯째 단계에서 다음 판정의 기준으로 되돌아갑니다.

1. **규칙 검사** (`writing_style_guard.py`). AI 가 파일에 글을 쓰기 직전에 끼어들어, 결산 관용구나 열거 도입구처럼 정규식으로 잡히는 꼴과 문장 길이 분포를 봅니다. 어긋나면 저장을 막고 목록을 돌려주므로 AI 가 바로 고쳐 다시 씁니다.
2. **읽는 판정** (`ai_tell_judge.py`). 규칙 검사를 통과한 글이 12문장 이상이면 배경에서 claude 에게 그 글을 내 글 표본과 견줘 읽게 하고, 의심 문장을 "강"과 "약"으로 짚게 합니다. 목록에 없는 꼴, 예를 들어 앞 내용을 그럴듯한 경구 한 줄로 닫는 문장은 이 단계에서 잡힙니다.
3. **등급은 코드가 매깁니다.** 모델에게 등급까지 맡기면 사람 글도 AI 글도 거의 다 B 가 나와서, 모델은 의심 문장만 짚고 등급은 1,000자당 "강" 의심 수로 계산합니다. 0.9 이상이면서 2곳 이상이면 C, 2.0 이상이면서 3곳 이상이면 D 이고, 1,000자가 안 되는 글은 1,000자로 쳐서 짧은 글이 지나치게 불리해지지 않게 했습니다.
4. **고쳐 쓴 문장** (`rewrite`). 짚은 문장마다 내 표본의 말투와 문장 길이를 따라 고친 문장을 냅니다. 문장을 지우는 안은 내지 않고, 글에 없는 사실이나 숫자를 지어내지 않게 지시합니다.
5. **돌려보내기** (`ai_tell_hooks.py stop`). AI 가 답변을 끝내려는 순간 C 나 D 등급이 있으면 답변을 막고 의심 문장을 돌려줍니다. 같은 파일은 사용자 입력 한 번에 두 번까지만 막고, 그 뒤는 다음 입력 때 알림으로 넘깁니다.
6. **수정 기록 학습** (`style_learn.py`). AI 가 쓴 파일을 사람이 직접 고치거나 표현을 말로 지적하면 그 기록이 쌓이고, 학습이 거기서 "AI 가 쓴 글에만 나오는 문장 꼴"을 후보로 뽑습니다. 후보는 시험을 두 번 거치는데, 내 글 표본에서 걸리면 버리고 서로 다른 AI 글 두 편 이상에서 안 보여도 버립니다. 살아남은 항목만 다음 판정 지시문에 실립니다.
7. **시험** (`ai_tell_eval.py`). 지시문이나 문턱값을 고치면 내 글 표본과 AI 글을 같이 판정해, 내 글을 AI 로 잘못 본 경우가 0 인지 확인합니다.

처음 학습을 만들 때는 "글쓴이가 어떻게 고쳤나"를 배우게 했는데, 그랬더니 글 짓는 규칙만 잔뜩 나왔고 그 규칙이 제 글까지 AI 티로 잡았습니다. 그래서 배우는 대상을 "AI 글에는 있고 내 글에는 없는 꼴"로 좁혔고, 6번의 두 번 시험이 그 경계를 지킵니다.

## 설치

파이썬 3.10 이상과 claude CLI(Claude Code)가 필요하고, claude 는 구독 계정으로 로그인돼 있어야 합니다.

```bash
git clone https://github.com/wnstjd878/ai-tell-harness.git
cd ai-tell-harness
python ai_tell_judge.py init
```

`init` 은 `~/.ai-tell` 폴더를 만들고 배경 판정을 켭니다. 폴더를 바꾸려면 환경변수 `AI_TELL_HOME` 을 주면 되고, 모든 기록과 설정이 그 아래에 쌓입니다.

| 경로 | 내용 |
|---|---|
| `samples/` | 내가 직접 쓴 글 표본(.txt). 판정의 기준 |
| `data/` | 판정 결과와 수정 기록 |
| `learned_rules.json` | 수정 기록에서 배운 AI 티 |
| `author_sentences.txt` | 내가 직접 쓴 문장 창고. 여기 있는 문장은 짚지 않음 |
| `profile.txt` | (선택) 글쓴이와 독자를 한두 줄로 |
| `keep.txt` | (선택) 잡지 말아야 할 내 말버릇, 한 줄에 하나 |
| `ban_words.txt` | (선택) 규칙 검사가 막을 낱말, 한 줄에 하나 |
| `rules/quick-rules.md` | (선택) 문법 층 규칙 |
| `judge.enabled` | 이 파일이 있을 때만 배경 판정과 돌려보내기가 돕니다 |

`rules/quick-rules.md` 에는 한국어 AI 티 패턴 70개를 10개 분류로 정리한 [im-not-ai](https://github.com/epoko77-ai/im-not-ai)(MIT)의 `skills/humanize-korean/references/quick-rules.md` 를 받아 두면 됩니다. 없어도 돌지만 있으면 판정이 문법 층 꼴까지 봅니다.

## 내 글 표본 넣기

판정은 "이 사람 글에는 안 나오는데 이 글에는 나오는 꼴"을 찾는 일이라, 표본이 없으면 평범한 한국어 글을 기준으로 삼아 정확도가 떨어집니다. `~/.ai-tell/samples/` 에 AI 가 한 글자도 쓰지 않은 내 글을 2,000자 이상씩 서너 편 넣고, 합쇼체와 평어체를 둘 다 쓰면 각각 한 편 이상 넣습니다.

넣은 뒤에는 문장 길이 기준을 만들고 시험을 한 번 돌립니다.

```bash
python style_profile.py build 내글 ~/.ai-tell/samples/*.txt
python ai_tell_eval.py --fresh
```

`--fresh` 는 claude 에게 아무 규칙 없이 글 여섯 편을 쓰게 해서 `data/eval/` 에 두고, 내 글 표본과 그 여섯 편을 같이 판정해 표로 보여 줍니다. 내 글이 C 나 D 로 나오면 그 글에서 짚인 문장을 보고 `keep.txt` 에 그 말버릇을 적거나 `author_sentences.txt` 에 그 문장을 넣습니다.

## 손으로 한 편 판정해 보기

```bash
python ai_tell_judge.py judge examples/ai_flower.txt --rewrite
```

`examples/README.md` 에 이 글을 실제로 돌린 출력이 있습니다.

## Claude Code 에 등록

`~/.claude/settings.json` 의 `hooks` 에 아래를 넣습니다. 경로는 저장소를 받은 자리로 바꿉니다.

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash",
        "hooks": [
          { "type": "command", "command": "python /path/to/ai-tell-harness/writing_style_guard.py" },
          { "type": "command", "command": "python /path/to/ai-tell-harness/ai_tell_hooks.py pre" }
        ] }
    ],
    "PostToolUse": [
      { "matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash",
        "hooks": [ { "type": "command", "command": "python /path/to/ai-tell-harness/ai_tell_hooks.py post" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python /path/to/ai-tell-harness/ai_tell_hooks.py stop", "timeout": 90 } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "python /path/to/ai-tell-harness/ai_tell_hooks.py prompt" } ] }
    ]
  }
}
```

`stop` 은 배경 판정이 끝나기를 최대 45초 기다리므로 `timeout` 을 그보다 넉넉하게 줍니다.

## Codex 에 등록

`~/.codex/hooks.json` 에 같은 넷을 넣는데, Claude Code 와 다른 점이 넷 있어서 하나라도 어기면 아무 경고 없이 안 돕니다.

1. 파일 맨 위에는 `description` 과 `hooks` 만 둡니다. 설명용 항목을 하나라도 더 넣으면 파일 전체를 못 읽어 등록이 0개가 됩니다.
2. `command` 안에 큰따옴표를 쓰지 않습니다. Codex 는 따옴표를 벗기지 않고 명령을 나누기 때문에, 경로에 공백이 있으면 따옴표 대신 공백 없는 경로로 옮깁니다.
3. 파일 편집 도구 이름은 `apply_patch` 이고 셸 도구는 끼어드는 스크립트에 `Bash` 라는 이름으로 넘어오므로, 일치 패턴에 `apply_patch|Bash` 를 둘 다 적습니다.
4. 등록한 뒤 Codex 화면에서 `/hooks` 를 열어 사람이 한 번 승인해야 돌고, 스크립트 내용이 바뀌면 다시 승인해야 합니다.

```json
{
  "description": "ai-tell-harness",
  "hooks": {
    "PreToolUse": [
      { "matcher": "apply_patch|Bash",
        "hooks": [
          { "type": "command", "command": "python /path/to/ai-tell-harness/writing_style_guard.py" },
          { "type": "command", "command": "python /path/to/ai-tell-harness/ai_tell_hooks.py pre" }
        ] }
    ],
    "PostToolUse": [
      { "matcher": "apply_patch|Bash",
        "hooks": [ { "type": "command", "command": "python /path/to/ai-tell-harness/ai_tell_hooks.py post" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python /path/to/ai-tell-harness/ai_tell_hooks.py stop" } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "python /path/to/ai-tell-harness/ai_tell_hooks.py prompt" } ] }
    ]
  }
}
```

승인 전에는 Codex 가 고친 파일에 "AI 가 손댄 표시"가 안 남아서 사람이 고친 것으로 잡히므로, 등록하고 바로 승인까지 끝냅니다.

## 학습 돌리기

```bash
python style_learn.py          # 병합안만 보여 주고 저장하지 않음
python style_learn.py --go     # 저장
python style_learn.py list     # 배운 항목 목록
python style_learn.py off 3    # 3번 항목을 끔. 같은 취지를 다시 만들지 않음
python style_learn.py rollback # 직전 저장 전으로 되돌림
```

하루 한 번 예약 실행(cron, launchd, 작업 스케줄러)에 `style_learn.py --go` 를 걸어 두면 쌓인 수정 기록을 차례로 배웁니다. 한 번에 15건까지 보고 남은 것은 다음 실행으로 넘깁니다.

## 끄는 법

- 배경 판정과 돌려보내기를 멈추려면 `~/.ai-tell/judge.enabled` 를 지웁니다. 수정 기록 저장은 계속됩니다.
- 규칙 검사를 한 번만 넘기려면 글 안에 `style-guard:off` 를 넣고, 통째로 끄려면 환경변수 `STYLE_GUARD=0` 을 줍니다.
- 배운 항목 하나가 자꾸 틀리게 짚으면 `style_learn.py off <번호>` 로 끕니다.

## 한계

- 판정 한 번에 입력이 약 4,000~13,000토큰(표본과 배운 항목이 실리는 양에 따라 다름) 들고 12~18초 걸립니다. 구독 한도 안에서 돌지만 긴 원고를 자주 쓰면 한도가 빨리 찹니다.
- 같은 글이라도 돌릴 때마다 짚는 문장이 조금씩 달라집니다.
- 처음 보는 내 문장은 짚힐 수 있습니다. 그럴 때 AI 는 고치지 않고 답변에 이유를 한 줄 적게 돼 있고, 그 문장을 `author_sentences.txt` 에 넣으면 다음부터는 짚지 않습니다.
- 12문장이 안 되는 짧은 글은 읽는 판정을 하지 않고 규칙 검사만 받습니다.
- 파일만 봅니다. 노션이나 구글 문서에서 고친 글은 수정 기록으로 들어오지 않습니다.
- AI 가 띄운 배경 작업이 답변이 끝난 뒤 파일을 쓰면 사람이 고친 것으로 잡힙니다.

## 라이선스

MIT. 문법 층 규칙을 가져다 쓰는 im-not-ai 도 MIT 입니다.
