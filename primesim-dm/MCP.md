# primesim-dm MCP 서버

primesim-dm의 검사 기능을 MCP 도구로 노출합니다. opencode(codemate) 같은 AI 툴이
"이 덱 SI 검사해 줘", "txp에 뭐가 붙어 있어?" 같은 질문을 받으면 도구를 골라 부릅니다.

- **읽기 전용입니다.** 덱을 고치지 않습니다. `make_termination`도 텍스트만 돌려줍니다.
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

모든 도구는 `deck_path`(덱 **절대 경로**)를 받습니다.

| 도구 | 답하는 질문 | 원래 기능 |
|---|---|---|
| `deck_summary` | 몇 개 읽었고, 어떤 문제가 몇 개 있나 | `lint --summary` |
| `check_si_fanout` | 소자가 2개가 아닌 signal 노드는? | `lint --si` |
| `find_floating_nets` | 한쪽만 연결된 노드는? | `floating-net` |
| `lint_findings` | 특정 종류(예: `port-count`)의 문제 목록 | `lint` |
| `net_info` | 이 노드에 뭐가 몇 번 포트로 붙어 있나 | graph 노드 클릭 |
| `instance_info` | 이 인스턴스는 무엇이고 포트마다 몇 개가 공유하나 | graph 박스 클릭 |
| `make_termination` | 종단 줄 만들어 줘 (R/C, 값) — 텍스트로만 | `terminate` |
| `render_graph` | 그림 파일 만들어 줘 (`.html` 절대 경로) | `graph` |

- 결과는 JSON이고, 목록은 기본 50개까지 보여주며 나머지 개수를 `not_shown`에 줍니다.
- 못 읽은 include가 있으면 `missing_includes`에 나옵니다. 이때 한쪽 연결 노드는 그 때문일 수 있습니다.
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
primesim_dm/mcp_server.py  tools.py를 MCP 도구로 등록 (mcp 1.x FastMCP / 2.x MCPServer 둘 다)
primesim-dm-mcp            어디서든 실행하는 런처
```

새 도구를 추가하려면 `tools.py`에 함수를 만들고 `mcp_server.py`의 `build()`에 `@server.tool()`로 등록합니다.
도구 설명(docstring)이 AI가 도구를 고르는 기준이니, **언제 쓰는지**를 한두 문장으로 분명히 적으세요.
