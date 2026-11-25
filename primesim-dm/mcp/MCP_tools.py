# =====================================================================
# primesim-dm tools for the in-house MCP.py
#
# Paste everything below into MCP.py.  Each tool runs its own script,
# scripts/dm_<tool>.py; put them in the scripts/ folder next to MCP.py with
#   python3 /proj/tools/primesim-dm/mcp/deploy.py <MCP.py folder>/scripts
# It uses what MCP.py already defines:
#   mcp, require_user_path, run_script, OUTPUT_PATH_DESC, OVERWRITE_DESC
# and needs these imports there (add the ones MCP.py does not have yet):
#   from pathlib import Path
#   from typing import Annotated, Any, Optional
#   from pydantic import Field
#
# Four tools: SI fan-out, the other errors, the graph, terminations.
# Tool names start with dm_ so they do not clash with the other tools.
# =====================================================================

# Site options, passed to every check: where the models live, which trees to
# keep out, which nets are power.  Same meaning as the CLI options.
DM_SITE_ARGS = [
    # "--search-dir", "/proj/models",
    # "--skip", "/pdk/",
    # "--opaque", "/io_model/",
    # "--keep-net", "^vdd", "--keep-net", "^vss",
]

DM_DECK_PATH_DESC = ("검사할 덱(.sp) 파일의 절대경로. "
                     "사용자가 직접 말한 경로만 넣는다.")
DM_KEEP_NET_DESC = ("검사에서 뺄 노드 이름의 정규식 목록. 예: 전원 노드 "
                    '["^vdd", "^vss"]. 사용자가 말하지 않았으면 비워 둔다.')
DM_LIMIT_DESC = ("목록으로 돌려줄 최대 개수. 나머지는 개수만 not_shown 으로 "
                 "알려준다.")


def _dm_args(deck, keep_net=None):
    args = ["--deck", deck, "--json"] + DM_SITE_ARGS
    for rx in keep_net or []:
        args += ["--keep-net", rx]
    return args


@mcp.tool()
def dm_check_si_fanout(
    deck_path: Annotated[Optional[str], Field(
        description=DM_DECK_PATH_DESC)] = None,
    expect: Annotated[int, Field(
        description="노드 하나에 붙어야 하는 소자 수. 기본 2 "
                    "(드라이버 1 + 리시버 1).")] = 2,
    keep_net: Annotated[Optional[list[str]], Field(
        description=DM_KEEP_NET_DESC)] = None,
    ignore_passives: Annotated[bool, Field(
        description="true 면 R/C/L 은 세지 않는다. 종단 저항까지 2개로 "
                    "볼 때만 true.")] = False,
    limit: Annotated[int, Field(description=DM_LIMIT_DESC)] = 50,
) -> dict[str, Any]:
    """SI 덱에서 소자 수가 expect(기본 2)가 아닌 signal 노드를 찾습니다.

    언제 사용하나:
    - SI 검사, 노드 연결 수 검사, stub(분기) 확인을 요청할 때

    사용자한테 받아야 하는 값:
    - deck_path(덱 경로)는 필수입니다.
      사용자가 말하지 않았으면 호출하지 말고 먼저 물어보세요.

    사용하지 않는 경우:
    - SI 덱이 아닐 때 (전원·제어 노드는 원래 2개가 아니므로 결과가 무의미합니다).

    출력 형식 (JSON):
    - status: "ok"
    - summary: 수행한 작업 한 줄 요약
    - total, too_few, too_many: 위반 노드 수 (1개 이하 / 3개 이상)
    - nets: [{"net", "count", "elements", "where"}] where 는 "파일:줄"
    - not_shown: limit 때문에 빠진 개수
    - missing_includes: 못 읽은 include 목록

    사용자에게 답할 때:
    - too_few(연결 누락 의심)와 too_many(분기 의심)를 나눠서 알려주세요.
    - 전원 노드(vdd 등)가 많이 섞여 있으면 keep_net 으로 빼고 다시 볼지 물어보세요.
    - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 사용자에게 필요한 값을 물어보세요.
    """
    src = require_user_path(deck_path, param="deck_path", what="검사할 덱 파일")
    args = _dm_args(src, keep_net)
    args += ["--expect", str(expect), "--limit", str(limit)]
    if ignore_passives:
        args.append("--ignore-passives")
    return run_script("dm_check_si_fanout.py", args)


@mcp.tool()
def dm_check_errors(
    deck_path: Annotated[Optional[str], Field(
        description=DM_DECK_PATH_DESC)] = None,
    limit: Annotated[int, Field(description=DM_LIMIT_DESC)] = 50,
) -> dict[str, Any]:
    """덱의 include 누락과 문법·구조 에러를 한 번에 검사합니다 (연결 수 검사는 제외).

    언제 사용하나:
    - 덱을 검사해 달라, 에러를 찾아 달라는 요청을 받았을 때
    - include 누락, 정의 없는 subckt, 포트 수 불일치, 이름 중복, 모델 파일 누락,
      읽지 못한 줄을 확인할 때

    사용자한테 받아야 하는 값:
    - deck_path(덱 경로)는 필수입니다.
      사용자가 말하지 않았으면 호출하지 말고 먼저 물어보세요.

    사용하지 않는 경우:
    - 노드당 소자 수(SI fan-out) 검사 (dm_check_si_fanout 을 쓰세요).
    - 한쪽만 연결된 노드 처리 (dm_make_termination 을 쓰세요).

    출력 형식 (JSON):
    - status: "ok"
    - summary: 수행한 작업 한 줄 요약
    - files_read, elements, nets: 읽은 파일 / 소자 / 노드 수
    - errors, warnings: 등급별 개수
    - by_kind: [{"severity", "code", "count"}] 종류별 개수
    - findings: [{"severity", "code", "message", "where"}] where 는 "절대경로:줄"
    - not_shown: limit 때문에 빠진 개수
    - missing_includes: [{"include", "from", "line"}] 못 읽은 include

    사용자에게 답할 때:
    - missing_includes 가 있으면 가장 먼저, 덱 일부를 못 읽어 검사가 불완전하다고 알려주세요.
    - errors / warnings 개수와 종류별 개수, 에러마다 위치(where)를 알려주세요.
    - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 사용자에게 필요한 값을 물어보세요.
    """
    src = require_user_path(deck_path, param="deck_path", what="검사할 덱 파일")
    args = _dm_args(src) + ["--limit", str(limit)]
    return run_script("dm_check_errors.py", args)


@mcp.tool()
def dm_make_termination(
    deck_path: Annotated[Optional[str], Field(
        description=DM_DECK_PATH_DESC)] = None,
    output_path: Annotated[Optional[str], Field(
        description=OUTPUT_PATH_DESC)] = None,
    kind: Annotated[str, Field(
        description="종단 소자 종류: rload(저항) 또는 cload(커패시터). "
                    "사용자가 말하지 않았으면 rload.")] = "rload",
    value: Annotated[str, Field(
        description="저항값 (kind=rload 일 때). 예: 50, 1k, 1T")] = "1T",
    value2: Annotated[str, Field(
        description="커패시터값 (kind=cload 일 때). 예: 5f")] = "1f",
    exclude: Annotated[Optional[list[str]], Field(
        description="종단하지 않을 노드 이름의 정규식 목록. 사용자가 "
                    "말하지 않았으면 비워 둔다.")] = None,
    overwrite: Annotated[bool, Field(description=OVERWRITE_DESC)] = False,
) -> dict[str, Any]:
    """한쪽만 연결된 노드마다 종단 소자(R 또는 C) 줄을 만들어 .inc 파일로 저장합니다. 덱은 고치지 않습니다.

    언제 사용하나:
    - 뜬 노드에 종단(termination)을 달아 달라고 할 때

    사용자한테 받아야 하는 값:
    - deck_path(덱 경로)와 output_path(.inc 저장 경로)는 필수입니다.
      사용자가 말하지 않은 값이 있으면 호출하지 말고 먼저 물어보세요.
    - kind 와 값(value / value2)은 사용자가 말한 대로 넣고, 없으면 기본값을 씁니다.

    사용하지 않는 경우:
    - 덱 일부를 못 읽은(missing include) 상태 — 이 도구가 거부합니다.
      include 를 먼저 해결하라고 안내하세요.

    출력 형식 (JSON):
    - status: "ok"
    - summary: 수행한 작업 한 줄 요약
    - count: 만든 종단 줄 수
    - nets: [{"net", "element", "ports", "where"}] 종단한 노드와 매달린 인스턴스·포트
    - not_shown: 목록에서 빠진 개수
    - created / modified: 새로 만든 / 덮어쓴 파일의 절대경로 목록

    사용자에게 답할 때:
    - 몇 개를 만들었는지와 저장 경로를 알려주세요.
    - 반드시 내용을 검토한 뒤 덱에 .include 하라고 알려주세요
      (안 쓰는 핀인지 빠뜨린 결선인지 도구는 구분하지 못합니다).
    - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 사용자에게 필요한 값을 물어보세요.
    """
    src = require_user_path(deck_path, param="deck_path", what="검사할 덱 파일")
    dst = require_user_path(output_path)
    args = _dm_args(src) + [
        "--output", dst, "--kind", kind, "--value", value, "--value2", value2]
    for rx in exclude or []:
        args += ["--exclude", rx]
    if overwrite:
        args.append("--overwrite")
    return run_script("dm_make_termination.py", args, watch_dirs=[str(Path(dst).parent)])


@mcp.tool()
def dm_render_graph(
    deck_path: Annotated[Optional[str], Field(
        description=DM_DECK_PATH_DESC)] = None,
    output_path: Annotated[Optional[str], Field(
        description=OUTPUT_PATH_DESC + " .html 로 끝나야 한다.")] = None,
    overwrite: Annotated[bool, Field(description=OVERWRITE_DESC)] = False,
) -> dict[str, Any]:
    """덱의 연결을 브라우저로 여는 HTML 그림 파일로 저장합니다.

    언제 사용하나:
    - 연결을 그림으로 보고 싶다고 할 때

    사용자한테 받아야 하는 값:
    - deck_path(덱 경로)와 output_path(.html 저장 경로)는 필수입니다.
      사용자가 말하지 않은 값이 있으면 호출하지 말고 먼저 물어보세요.

    사용하지 않는 경우:
    - 문제 목록이나 개수만 필요할 때 (다른 도구가 더 빠릅니다).

    출력 형식 (JSON):
    - status: "ok"
    - summary: 수행한 작업 한 줄 요약
    - created / modified: 새로 만든 / 덮어쓴 파일의 절대경로 목록

    사용자에게 답할 때:
    - 저장된 경로를 알려주고, 브라우저로 열면 빨간 노드가 한쪽만 연결된 노드라고 알려주세요.
    - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 사용자에게 필요한 값을 물어보세요.
    """
    src = require_user_path(deck_path, param="deck_path", what="그릴 덱 파일")
    dst = require_user_path(output_path)
    args = _dm_args(src) + ["--output", dst]
    if overwrite:
        args.append("--overwrite")
    return run_script("dm_render_graph.py", args, watch_dirs=[str(Path(dst).parent)])
