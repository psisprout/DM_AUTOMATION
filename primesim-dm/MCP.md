# primesim-dm MCP 도구 (사내 MCP.py 양식)

primesim-dm 검사 기능을 사내 MCP 서버(codemate / opencode)의 도구로 붙이는 방법입니다.
사내 양식대로 두 부분으로 나뉩니다.

| 파일 | 어디에 넣나 | 하는 일 |
|---|---|---|
| `mcp/MCP_tools.py` | 사내 `MCP.py` 에 **붙여 넣기** | `@mcp.tool()` 정의 9개. 인자·설명·docstring만 있고, 본문은 `run_script` 호출 |
| `mcp/script/dm_deck.py` | 사내 `script/` 폴더에 **복사** | 실제 실행. primesim-dm 을 불러 결과를 JSON 으로 출력 |

- **덱은 절대 고치지 않습니다.** 파일을 쓰는 도구는 사용자가 말한 `output_path`에만 쓰고,
  이미 있는 파일은 `overwrite=true`일 때만 덮어씁니다.
- `require_user_path`, `run_script`, `OUTPUT_PATH_DESC`, `OVERWRITE_DESC` 는 사내 `MCP.py` 에 있는 것을 그대로 씁니다.

## 1. 붙이는 순서

1. `mcp/MCP_tools.py` 의 내용을 사내 `MCP.py` 끝에 붙여 넣습니다.
   - `MCP.py` 에 아래 import 가 없으면 추가합니다.
     ```python
     from pathlib import Path
     from typing import Annotated, Any, Optional
     from pydantic import Field
     ```
2. `mcp/script/dm_deck.py` 를 사내 `script/` 폴더에 복사합니다.
3. MCP 서버를 띄우는 환경에 `PRIMESIM_DM_HOME` 을 primesim-dm 폴더로 설정합니다.
   ```bash
   export PRIMESIM_DM_HOME=/proj/tools/primesim-dm
   ```
4. (선택) 붙여 넣은 코드 맨 위 `DM_SITE_ARGS` 에 사이트 옵션을 적습니다. 모든 도구에 똑같이 붙습니다.
   ```python
   DM_SITE_ARGS = ["--skip", "/pdk/", "--opaque", "/io_model/",
                   "--keep-net", "^vdd", "--keep-net", "^vss"]
   ```

## 2. 도구 목록

이름이 다른 사내 도구와 겹치지 않게 모두 `dm_` 으로 시작합니다. 모든 도구는 `deck_path`(덱 **절대 경로**)를 받습니다.

| 도구 | 답하는 질문 | 원래 기능 |
|---|---|---|
| `dm_deck_summary` | 몇 개 읽었고, 어떤 문제가 몇 개 있나 | `lint --summary` |
| `dm_check_si_fanout` | 소자가 2개가 아닌 signal 노드는? | `lint --si` |
| `dm_find_floating_nets` | 한쪽만 연결된 노드는? | `floating-net` |
| `dm_lint_findings` | 특정 종류(예: `port-count`)의 문제 목록 | `lint` |
| `dm_net_info` | 이 노드에 뭐가 몇 번 포트로 붙어 있나 | graph 노드 클릭 |
| `dm_instance_info` | 이 인스턴스는 무엇이고 포트마다 몇 개가 공유하나 | graph 박스 클릭 |
| `dm_save_lint_report` | lint 리포트를 파일로 저장해 줘 | `lint -o report.txt` |
| `dm_make_termination` | 종단 .inc 만들어 줘 (R/C, 값) | `terminate -o term.inc` |
| `dm_render_graph` | 그림 파일 만들어 줘 (`.html`) | `graph -o graph.html` |

## 3. 스크립트 단독 실행 (확인용)

MCP 없이 스크립트만 돌려 볼 수 있습니다. 출력은 항상 JSON 하나입니다.

```bash
python3 script/dm_deck.py si-fanout --deck /proj/sim/lpddr_write.sp --json
python3 script/dm_deck.py terminate --deck /proj/sim/lpddr_write.sp --output /proj/sim/term.inc --kind cload --value2 5f
```

| 명령 | 도구 |
|---|---|
| `summary` / `si-fanout` / `floating` / `findings` | 검사 |
| `net --net X` / `instance --name X` | 노드·인스턴스 하나 보기 |
| `lint-report` / `terminate` / `graph` | `--output` 에 파일 저장 (`--overwrite` 없으면 기존 파일 보호) |

- 성공: `{"status": "ok", "summary": "...", ...}`, 종료 코드 0
- 실패: `{"status": "error", "message": "..."}`, 종료 코드 1.
  사용자에게 물어봐야 하는 경우(경로 없음, 파일 이미 있음 등)는 메시지가 `[사용자 확인 필요]` 로 시작합니다.

> `run_script` 가 스크립트 실패를 어떻게 받는지(종료 코드인지, `status` 필드인지)는 사내 구현을 보지 못했습니다.
> 둘 다 맞춰 두었으니, 실패 메시지가 에이전트에게 안 보이면 `run_script` 쪽 처리 방식을 알려 주세요.

## 4. 구조

```
mcp/MCP_tools.py            사내 MCP.py 에 붙일 @mcp.tool() 정의
mcp/script/dm_deck.py       사내 script/ 에 복사할 실행 스크립트 (PRIMESIM_DM_HOME 으로 패키지를 찾음)
primesim_dm/tool_cli.py     dm_deck.py 의 본체: 명령 해석, JSON 출력, 덮어쓰기 보호
primesim_dm/tools.py        질문 하나 = 함수 하나. dict 반환, stdlib 만
tests/test_tools.py, tests/test_tool_cli.py
```

새 도구를 추가하려면 `tools.py` 에 함수 → `tool_cli.py` 에 명령 → `MCP_tools.py` 에 `@mcp.tool()` 순서로 넣습니다.
docstring 은 기존 도구처럼 `언제 사용하나` / `사용자한테 받아야 하는 값` / `사용하지 않는 경우` /
`출력 형식 (JSON)` / `사용자에게 답할 때` 다섯 섹션으로 쓰세요. 에이전트가 도구를 고르는 기준이 이 문장입니다.
