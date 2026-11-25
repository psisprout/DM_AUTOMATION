# primesim-dm MCP 도구 (사내 MCP.py 양식)

primesim-dm 검사 기능을 사내 MCP 서버(codemate / opencode)의 도구로 붙이는 방법입니다.
사내 양식대로 두 부분으로 나뉩니다.

| 파일 | 어디에 넣나 | 하는 일 |
|---|---|---|
| `mcp/MCP_tools.py` | 사내 `MCP.py` 에 **붙여 넣기** | `@mcp.tool()` 정의 4개. 인자·설명·docstring만 있고, 본문은 `run_script` 호출 |
| `mcp/scripts/dm_*.py` (4개) | 사내 `scripts/` 에 **배포** | 도구마다 하나씩, 실제 실행. 결과를 JSON 으로 출력 |
| `primesim_dm_*.py` (8개) | 사내 `scripts/` 에 **함께 배포** | 위 스크립트들이 쓰는 검사 코드. `primesim_dm/` 패키지를 평평한 파일로 바꾼 것 |

`scripts/` 에는 **하위 폴더 없이 파일만** 들어갑니다 (실행해도 `__pycache__/` 가 생기지 않습니다).

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
2. 배포 스크립트로 사내 `scripts/` 폴더에 파일을 넣습니다. 환경변수 설정은 필요 없습니다.
   ```bash
   python3 /proj/tools/primesim-dm/mcp/deploy.py <사내MCP>/scripts
   ```
   이렇게 들어갑니다. 스크립트는 옆의 `primesim_dm_*.py` 를 `Path(__file__).parent` 기준으로 찾습니다.
   ```
   <사내MCP>/MCP.py
   <사내MCP>/scripts/dm_check_si_fanout.py     ┐
   <사내MCP>/scripts/dm_check_errors.py        │ 도구 스크립트 4개
   <사내MCP>/scripts/dm_make_termination.py    │
   <사내MCP>/scripts/dm_render_graph.py        ┘
   <사내MCP>/scripts/primesim_dm_deck.py       ┐
   <사내MCP>/scripts/primesim_dm_check.py      │ 검사 코드 8개
   ...                                         ┘
   ```
   - 이미 있는 파일은 덮어쓰지 않고 멈춥니다. primesim-dm 을 고친 뒤 다시 넣을 때는 `--overwrite` 를 붙이세요.
   - 예전에 배포한 도구 스크립트(`dm_net_info.py` 등)가 남아 있으면 이름을 알려줍니다. 지워도 됩니다.
   - 기준 패턴/옵션 파일 경로(`references.py`)는 primesim-dm 쪽에서 고친 뒤 다시 배포하세요.
     사내 `scripts/primesim_dm_references.py` 를 직접 고치면 다음 배포 때 덮어써집니다.
3. (선택) 붙여 넣은 코드 맨 위 `DM_SITE_ARGS` 에 사이트 옵션을 적습니다. 모든 도구에 똑같이 붙습니다.
   ```python
   DM_SITE_ARGS = ["--skip", "/pdk/", "--opaque", "/io_model/",
                   "--keep-net", "^vdd", "--keep-net", "^vss"]
   ```

## 2. 도구 목록

이름이 다른 사내 도구와 겹치지 않게 모두 `dm_` 으로 시작합니다. 모든 도구는 `deck_path`(덱 **절대 경로**)를 받습니다.

에이전트가 이것저것 부르다 오래 걸리지 않도록 **큰 일 네 개만** 도구로 둡니다.
한 번 부르면 판단에 필요한 정보(위치, 붙은 소자, 못 읽은 include)가 결과에 같이 들어 있습니다.

| 도구 (= 스크립트 이름) | 하는 일 | 원래 기능 |
|---|---|---|
| `dm_check_si_fanout` | 소자가 2개가 아닌 signal 노드 (부족 / 초과, 붙은 소자, 위치) | `lint --si` |
| `dm_check_errors` | include 누락, 정의 없는 subckt, 포트 수 불일치, 이름 중복, 모델 파일 누락, 읽지 못한 줄 | `lint` (연결 수 검사 제외) |
| `dm_render_graph` | 연결 그림 `.html` 저장 | `graph -o graph.html` |
| `dm_make_termination` | 한쪽만 연결된 노드에 R/C 종단 `.inc` 저장 + 대상 노드 목록 | `terminate -o term.inc` |

이보다 큰 흐름(예: "에러 확인 → SI 검사 → 종단까지 한 번에")은 이 도구들을 순서대로 부르는 skill 로 만들면 됩니다.

## 3. 스크립트 규약 지키는 방식

| 규약 | dm_*.py 스크립트 |
|---|---|
| 위치 `scripts/`, `.py` | 도구마다 `scripts/dm_<도구>.py` 하나씩 |
| 입력은 argparse 옵션만 | 모든 값이 옵션. `input()` 없음 |
| `--json` 이면 마지막 줄에 JSON 한 줄 | `{"status": "ok", "summary": ..., ...}` 또는 `{"status": "error", "message": ...}` |
| 진행·디버그 메시지는 stderr | `[primesim-dm] ...` 로 stderr 에만 |
| 종료 코드 성공 0, 실패 0 아닌 값 | 성공 0, 실패 2 (예상 못 한 오류도 JSON 으로 답하고 2) |
| 결과의 경로는 절대경로 | `deck`, `output`, `where`("절대경로:줄"), `missing_includes[].from` 모두 절대경로. 상대경로 입력은 거부 |
| 덮어쓰기는 `--overwrite` 있을 때만 | 파일이 있으면 `[사용자 확인 필요] 파일이 이미 있습니다` 로 실패 |
| 실행 시간 60초 이내 | `--timeout` 기본 55초. 넘으면 중단하고 JSON 오류로 답함 |
| 자기 리소스는 `Path(__file__).parent` 기준 | 검사 코드 `primesim_dm_*.py` 를 `Path(__file__).parent` 에서 찾음. 작업 폴더(cwd)나 환경변수는 쓰지 않음 |

단독으로 돌려 볼 수도 있습니다.

```bash
python3 scripts/dm_check_si_fanout.py --deck /proj/sim/lpddr_write.sp --json
python3 scripts/dm_make_termination.py --deck /proj/sim/lpddr_write.sp --output /proj/sim/term.inc --kind cload --value2 5f --json
python3 scripts/dm_check_errors.py --help    # 스크립트마다 자기 옵션만 받습니다
```

> 덱이 아주 커서 55초를 넘기면 `--skip` / `--opaque` 로 PDK·모델 안쪽을 읽지 않게 하세요 (`DM_SITE_ARGS`).

## 4. 구조

```
mcp/MCP_tools.py            사내 MCP.py 에 붙일 @mcp.tool() 정의 4개
mcp/scripts/dm_*.py         사내 scripts/ 에 들어갈 도구별 스크립트 4개 (몇 줄짜리 입구)
mcp/deploy.py               배포: dm_*.py 복사 + primesim_dm/ 을 primesim_dm_*.py 평평한 파일로 변환
primesim_dm/tool_cli.py     스크립트들의 본체: 옵션, JSON 출력, 덮어쓰기 보호, 시간 제한
primesim_dm/tools.py        질문 하나 = 함수 하나. dict 반환, stdlib 만
tests/test_tools.py, tests/test_tool_cli.py, tests/test_deploy.py
```

새 도구를 추가하려면 `tools.py` 에 함수 → `tool_cli.py` 의 `SCRIPTS` 에 스크립트 이름과 옵션 →
`mcp/scripts/dm_<도구>.py` (기존 것을 복사해 이름만 바꿈) → `MCP_tools.py` 에 `@mcp.tool()` 순서로 넣습니다.
docstring 은 기존 도구처럼 `언제 사용하나` / `사용자한테 받아야 하는 값` / `사용하지 않는 경우` /
`출력 형식 (JSON)` / `사용자에게 답할 때` 다섯 섹션으로 쓰세요. 에이전트가 도구를 고르는 기준이 이 문장입니다.
