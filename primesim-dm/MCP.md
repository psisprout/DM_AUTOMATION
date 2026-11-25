# primesim-dm MCP 서버

primesim-dm의 검사 기능을 MCP 도구로 노출합니다. opencode(codemate) 같은 AI 툴이
"이 덱 SI 검사해 줘", "txp에 뭐가 붙어 있어?" 같은 질문을 받으면 도구를 골라 부릅니다.

- **덱은 절대 고치지 않습니다.** 파일을 쓰는 도구(`save_lint_report`, `make_termination`, `render_graph`)는
  사용자가 말한 `output_path`에만 쓰고, 이미 있는 파일은 `overwrite=true`일 때만 덮어씁니다.
- 사내 양식을 따릅니다: 인자마다 `Annotated[..., Field(description=...)]`, docstring 다섯 섹션,
  사용자가 말하지 않은 경로는 `[사용자 확인 필요]` 오류로 되돌려 AI가 먼저 묻게 합니다.
- 연결 방식은 **stdio**입니다. AI 툴이 같은 리눅스 머신에서 서버를 직접 띄웁니다.

## 1. 준비

MCP 서버만 **Python 3.10 이상 + `mcp` 패키지**가 필요합니다. CLI(`lint` / `graph` / `terminate`)는 지금처럼 3.6에서도 돕니다.

```bash
python3.11 -m venv ~/venv-mcp
~/venv-mcp/bin/pip install mcp          # 1.x, 2.x 둘 다 동작
```

동작 확인 (아무 출력 없이 대기하면 정상, `Ctrl+C`로 종료):

```bash
~/venv-mcp/bin/python /proj/tools/primesim-dm/primesim-dm-mcp
```

## 2. opencode에 등록

프로젝트의 `opencode.json` (또는 전역 설정 `~/.config/opencode/opencode.json`):

```json
{
  "mcp": {
    "primesim-dm": {
      "type": "local",
      "command": [
        "/home/USER/venv-mcp/bin/python",
        "/proj/tools/primesim-dm/primesim-dm-mcp",
        "--skip", "/pdk/",
        "--opaque", "/io_model/",
        "--keep-net", "^vdd", "--keep-net", "^vss"
      ],
      "enabled": true
    }
  }
}
```

> 이 설정 형식은 opencode 공식 문서를 확인하지 못한 채 적었습니다 (이 환경에서 opencode.ai 접속이 막혀 있었음).
> 안 붙으면 사내 opencode 문서의 MCP 설정 항목과 키 이름(`type`, `command`)을 대조해 주세요.

**서버 옵션**은 사이트 설정이라 여기서 한 번만 줍니다. 도구를 부를 때마다 넘기지 않습니다.

| 옵션 | 뜻 (CLI와 같음) |
|---|---|
| `--search-dir DIR` | include 파일을 찾을 폴더 추가 |
| `--opaque REGEX` | `.subckt` 포트만 읽을 파일 |
| `--skip REGEX` | 아예 안 읽을 파일 |
| `--max-depth N` | include를 N단계까지만 |
| `--profile full` | M, D 등 모든 소자 읽기 |
| `--keep-net REGEX` | 모든 도구에서 뺄 노드 (전원 등) |

## 3. 도구 목록

모든 도구는 `deck_path`(덱 **절대 경로**)를 받습니다. 결과에는 항상 `status`와 한 줄 `summary`가 있고,
파일을 쓴 도구는 `created` / `modified`에 경로를 돌려줍니다.

| 도구 | 답하는 질문 | 원래 기능 |
|---|---|---|
| `deck_summary` | 몇 개 읽었고, 어떤 문제가 몇 개 있나 | `lint --summary` |
| `check_si_fanout` | 소자가 2개가 아닌 signal 노드는? | `lint --si` |
| `find_floating_nets` | 한쪽만 연결된 노드는? | `floating-net` |
| `lint_findings` | 특정 종류(예: `port-count`)의 문제 목록 | `lint` |
| `net_info` | 이 노드에 뭐가 몇 번 포트로 붙어 있나 | graph 노드 클릭 |
| `instance_info` | 이 인스턴스는 무엇이고 포트마다 몇 개가 공유하나 | graph 박스 클릭 |
| `save_lint_report` | lint 리포트를 파일로 저장해 줘 | `lint -o report.txt` |
| `make_termination` | 종단 .inc 만들어 줘 (R/C, 값) | `terminate -o term.inc` |
| `render_graph` | 그림 파일 만들어 줘 (`.html`) | `graph -o graph.html` |

- 결과는 JSON이고, 목록은 기본 50개까지 보여주며 나머지 개수를 `not_shown`에 줍니다.
- 못 읽은 include가 있으면 `missing_includes`에 나옵니다. 이때 한쪽 연결 노드는 그 때문일 수 있습니다.
- 경로를 안 줬거나 상대경로면 `[사용자 확인 필요] ... 사용자에게 절대경로를 물어보세요.` 오류가 갑니다.
- 저장할 파일이 이미 있으면 `[사용자 확인 필요] 파일이 이미 있습니다 ...`로 덮어쓸지 묻게 합니다.
- 없는 노드·인스턴스 이름을 주면 오류와 함께 비슷한 이름을 알려줍니다.
- 같은 덱을 연달아 물으면 다시 읽지 않습니다. 파일이 바뀌면 자동으로 다시 읽습니다.

## 4. 사용 예

opencode에서:

```
/proj/sim/lpddr_write.sp SI 검사해 줘. 3개 이상 붙은 노드는 어디서 갈라지는지도 알려줘.
```

→ `check_si_fanout` → 3개짜리 노드마다 `net_info` → 분기한 인스턴스를 정리해서 답합니다.

## 5. 구조

```
primesim_dm/tools.py       질문 하나 = 함수 하나. dict 반환, stdlib만 (테스트: tests/test_tools.py)
primesim_dm/mcp_server.py  tools.py를 사내 양식의 MCP 도구로 등록 (mcp 1.x FastMCP / 2.x MCPServer 둘 다)
                           require_user_path, DECK_PATH_DESC / OUTPUT_PATH_DESC / OVERWRITE_DESC 도 여기
primesim-dm-mcp            어디서든 실행하는 런처
```

새 도구를 추가하려면 `tools.py`에 함수를 만들고 `mcp_server.py`의 `build()`에 `@server.tool()`로 등록합니다.
docstring은 기존 도구처럼 `언제 사용하나` / `사용자한테 받아야 하는 값` / `사용하지 않는 경우` /
`출력 형식 (JSON)` / `사용자에게 답할 때` 다섯 섹션으로 쓰세요. AI가 도구를 고르는 기준이 이 문장입니다.

사내 공용 모듈에 `require_user_path` / `run_script`가 이미 있다면, `mcp_server.py`의 같은 이름 함수를
그 import로 바꾸면 됩니다. 이 서버는 스크립트를 subprocess로 부르지 않고 같은 프로세스에서 함수를
바로 호출합니다 (덱을 한 번 읽어 두고 다음 질문에 재사용하기 위해서).
