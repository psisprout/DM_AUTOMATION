"""MCP server: the deck checks as tools an assistant can call one at a time.

    python3 -m primesim_dm.mcp_server [--skip REGEX] [--opaque REGEX] ...

Speaks MCP over stdio, so the client (opencode, Claude Code, ...) starts it
as a subprocess on the same machine as the decks.  Needs the ``mcp`` package
(Python 3.10+); the rest of primesim-dm still needs nothing but the stdlib.

Tools follow the in-house layout: every argument is ``Annotated`` with a
``Field(description=...)``, every docstring has the same five sections, a
path the user has to supply goes through :func:`require_user_path`, and a
file is written only to an ``output_path`` the user named, replaced only with
``overwrite``.  Every result carries ``status`` and a one-line ``summary``;
tools that write list the files in ``created`` / ``modified``.

Read options (``--search-dir`` / ``--opaque`` / ``--skip`` / ``--max-depth``
/ ``--profile`` / ``--keep-net``) are given once here, in the client's
launch config, because they describe the site rather than the question.
No tool ever edits a deck.
"""

import argparse
import os
import sys
from typing import Annotated, Any, Optional

from pydantic import Field

from . import cli as cli_mod
from . import deck as deck_mod
from . import tools

try:                                    # mcp 2.x
    from mcp.server import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:                     # mcp 1.x
    try:
        from mcp.server.fastmcp import FastMCP as _Server
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError:
        _Server = None
        ToolError = ValueError

NEED_USER = "[사용자 확인 필요]"

INSTRUCTIONS = """\
기존 PrimeSim / HSPICE 덱(.sp)의 결선을 검사하는 도구들입니다. 덱은 수정하지 않습니다.
- 덱 경로(deck_path)와 저장 경로(output_path)는 사용자가 직접 말한 절대경로만 씁니다.
  말하지 않았으면 추측하지 말고 먼저 물어보세요.
- 처음에는 deck_summary 로 전체를 보고, SI 덱이면 check_si_fanout 을 씁니다.
- 보고된 노드나 인스턴스는 net_info / instance_info 로 따라갑니다.
- 결과의 missing_includes 가 비어 있지 않으면 덱 일부를 못 읽은 것이니 가장 먼저 알려주세요.
- "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 필요한 값을 사용자에게 물어보세요.
"""

DECK_PATH_DESC = ("검사할 덱(.sp) 파일의 절대경로. "
                  "사용자가 직접 말한 경로만 넣는다.")
OUTPUT_PATH_DESC = ("결과를 저장할 파일의 절대경로. "
                    "사용자가 직접 말한 경로만 넣는다.")
OVERWRITE_DESC = ("저장 경로에 파일이 이미 있을 때 덮어쓸지 여부. "
                  "사용자가 덮어쓰라고 말했을 때만 true.")
KEEP_NET_DESC = ("검사에서 뺄 노드 이름의 정규식 목록. 예: 전원 노드 "
                 '["^vdd", "^vss"]. 사용자가 말하지 않았으면 비워 둔다.')
LIMIT_DESC = "목록으로 돌려줄 최대 개수. 나머지는 개수만 not_shown 으로 알려준다."


def require_user_path(value, param="output_path", what="저장할 파일",
                      must_exist=False, suffix=None):
    """A path the user has to give: absolute, and there if it must be.

    Anything else is sent back as "[사용자 확인 필요]" so the assistant asks
    the user instead of guessing a path, which is the one thing it must not
    do with a file it is about to read or write.
    """
    if not value or not str(value).strip():
        raise ToolError("%s %s 경로(%s)가 없습니다. 사용자에게 절대경로를 "
                        "물어보세요." % (NEED_USER, what, param))
    path = str(value).strip()
    if not os.path.isabs(path):
        raise ToolError("%s %s 경로(%s)가 절대경로가 아닙니다: %s. 사용자에게 "
                        "절대경로를 물어보세요." % (NEED_USER, what, param, path))
    if must_exist and not os.path.isfile(path):
        raise ToolError("%s %s 이(가) 없습니다: %s. 경로가 맞는지 사용자에게 "
                        "확인하세요." % (NEED_USER, what, path))
    if suffix and not path.lower().endswith(suffix):
        raise ToolError("%s %s 경로(%s)는 %s 로 끝나야 합니다: %s"
                        % (NEED_USER, what, param, suffix, path))
    return path


def _run(fn):
    """Run one question; a DeckError becomes a tool error with its message.

    ToolError marks the result as an error for the client (isError) and,
    unlike any other exception, passes the message through, so the
    assistant reads "no such net; similar: ..." rather than a bare failure.
    """
    try:
        return fn()
    except tools.OutputExists as exc:
        raise ToolError("%s 파일이 이미 있습니다: %s. 덮어쓸지(overwrite=true) "
                        "다른 경로를 쓸지 사용자에게 물어보세요."
                        % (NEED_USER, exc.path))
    except tools.DeckError as exc:
        raise ToolError(str(exc))


def _ok(summary, **fields):
    out = {"status": "ok", "summary": summary}
    out.update(fields)
    return out


def build(opts):
    if _Server is None:
        raise SystemExit("the 'mcp' package is not installed: "
                         "pip install mcp  (needs Python 3.10+)")
    server = _Server("primesim-dm", instructions=INSTRUCTIONS)

    @server.tool()
    def deck_summary(
        deck_path: Annotated[Optional[str], Field(
            description=DECK_PATH_DESC)] = None,
        si: Annotated[bool, Field(
            description="true 면 SI 규칙(노드당 소자 2개) 위반도 센다. "
                        "SI 덱이라고 했을 때만 true.")] = False,
    ) -> dict[str, Any]:
        """덱을 읽어 파일·소자·노드 수와 문제 종류별 개수를 알려줍니다.

        언제 사용하나:
        - 덱을 검사해 달라는 요청을 처음 받았을 때 (가장 먼저 부르는 도구)
        - 문제가 몇 개, 어떤 종류가 있는지 개수만 알고 싶어할 때

        사용자한테 받아야 하는 값:
        - deck_path(덱 경로)는 필수입니다.
          사용자가 말하지 않았으면 호출하지 말고 먼저 물어보세요.

        사용하지 않는 경우:
        - 문제 하나하나의 목록이 필요할 때 (lint_findings, check_si_fanout,
          find_floating_nets 를 쓰세요).

        출력 형식 (JSON):
        - status: "ok"
        - summary: 수행한 작업 한 줄 요약
        - files_read, elements, nets: 읽은 파일 / 소자 / 노드 수
        - errors, warnings, notes: 등급별 개수
        - by_kind: [{"severity", "code", "count"}] 종류별 개수
        - missing_includes: 못 읽은 include 목록

        사용자에게 답할 때:
        - errors / warnings 개수와 by_kind 상위 몇 개를 알려주세요.
        - missing_includes 가 있으면 가장 먼저, 검사가 불완전하다고 알려주세요.
        - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 필요한 값을 물어보세요.
        """
        src = require_user_path(deck_path, param="deck_path",
                                what="검사할 덱 파일", must_exist=True)
        r = _run(lambda: tools.deck_summary(src, opts, si=si))
        return _ok("%s: 소자 %d개, 노드 %d개, error %d / warn %d"
                   % (os.path.basename(src), r["elements"], r["nets"],
                      r["errors"], r["warnings"]), **r)

    @server.tool()
    def check_si_fanout(
        deck_path: Annotated[Optional[str], Field(
            description=DECK_PATH_DESC)] = None,
        expect: Annotated[int, Field(
            description="노드 하나에 붙어야 하는 소자 수. 기본 2 "
                        "(드라이버 1 + 리시버 1).")] = 2,
        keep_net: Annotated[Optional[list[str]], Field(
            description=KEEP_NET_DESC)] = None,
        ignore_passives: Annotated[bool, Field(
            description="true 면 R/C/L 은 세지 않는다. 종단 저항까지 2개로 "
                        "볼 때만 true.")] = False,
        limit: Annotated[int, Field(description=LIMIT_DESC)] = 50,
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
        - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 필요한 값을 물어보세요.
        """
        src = require_user_path(deck_path, param="deck_path",
                                what="검사할 덱 파일", must_exist=True)
        r = _run(lambda: tools.check_si_fanout(
            src, opts, expect=expect, keep_net=keep_net,
            ignore_passives=ignore_passives, limit=limit))
        return _ok("소자 수가 %d개가 아닌 노드 %d개 (부족 %d, 초과 %d)"
                   % (expect, r["total"], r["too_few"], r["too_many"]), **r)

    @server.tool()
    def find_floating_nets(
        deck_path: Annotated[Optional[str], Field(
            description=DECK_PATH_DESC)] = None,
        keep_net: Annotated[Optional[list[str]], Field(
            description=KEEP_NET_DESC)] = None,
        limit: Annotated[int, Field(description=LIMIT_DESC)] = 50,
    ) -> dict[str, Any]:
        """소자 하나에만 연결된 노드(한쪽만 연결된 노드)를 찾습니다.

        언제 사용하나:
        - 뜬 노드, floating net, 연결 안 된 핀을 찾아 달라고 할 때
        - 종단(termination)을 달기 전에 대상 노드를 확인할 때

        사용자한테 받아야 하는 값:
        - deck_path(덱 경로)는 필수입니다.
          사용자가 말하지 않았으면 호출하지 말고 먼저 물어보세요.

        사용하지 않는 경우:
        - SI 규칙(노드당 2개)으로 보고 싶을 때 (check_si_fanout 을 쓰세요).

        출력 형식 (JSON):
        - status: "ok"
        - summary: 수행한 작업 한 줄 요약
        - total: 한쪽만 연결된 노드 수
        - nets: [{"net", "element", "ports", "where"}]
        - not_shown: limit 때문에 빠진 개수
        - missing_includes: 못 읽은 include 목록

        사용자에게 답할 때:
        - 노드마다 어느 인스턴스의 몇 번 포트인지 함께 알려주세요.
        - missing_includes 가 있으면 일부는 그 때문일 수 있다고 알려주세요.
        - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 필요한 값을 물어보세요.
        """
        src = require_user_path(deck_path, param="deck_path",
                                what="검사할 덱 파일", must_exist=True)
        r = _run(lambda: tools.find_floating_nets(
            src, opts, keep_net=keep_net, limit=limit))
        return _ok("한쪽만 연결된 노드 %d개" % r["total"], **r)

    @server.tool()
    def lint_findings(
        deck_path: Annotated[Optional[str], Field(
            description=DECK_PATH_DESC)] = None,
        code: Annotated[Optional[str], Field(
            description="이 종류만 보기. 예: missing-include, "
                        "undefined-subckt, port-count, duplicate-name, "
                        "floating-net. 모두 보려면 비워 둔다.")] = None,
        severity: Annotated[Optional[str], Field(
            description="이 등급만 보기: error, warn, info 중 하나. "
                        "모두 보려면 비워 둔다.")] = None,
        si: Annotated[bool, Field(
            description="true 면 SI 규칙 위반도 포함한다.")] = False,
        limit: Annotated[int, Field(description=LIMIT_DESC)] = 50,
    ) -> dict[str, Any]:
        """lint 검사 결과를 한 줄씩 목록으로 돌려줍니다. 종류나 등급으로 거를 수 있습니다.

        언제 사용하나:
        - include 누락, 정의 없는 subckt, 포트 수 불일치 같은 구조 문제를 볼 때
        - deck_summary 에서 본 특정 종류의 상세 목록이 필요할 때

        사용자한테 받아야 하는 값:
        - deck_path(덱 경로)는 필수입니다.
          사용자가 말하지 않았으면 호출하지 말고 먼저 물어보세요.

        사용하지 않는 경우:
        - 리포트를 파일로 저장해 달라는 요청 (save_lint_report 를 쓰세요).

        출력 형식 (JSON):
        - status: "ok"
        - summary: 수행한 작업 한 줄 요약
        - total: 조건에 맞는 문제 수
        - findings: [{"severity", "code", "message", "where"}]
        - not_shown: limit 때문에 빠진 개수

        사용자에게 답할 때:
        - 문제마다 위치(where, "파일:줄")를 함께 알려주세요.
        - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 필요한 값을 물어보세요.
        """
        src = require_user_path(deck_path, param="deck_path",
                                what="검사할 덱 파일", must_exist=True)
        r = _run(lambda: tools.lint_findings(
            src, opts, code=code, severity=severity, si=si, limit=limit))
        return _ok("조건에 맞는 문제 %d개" % r["total"], **r)

    @server.tool()
    def net_info(
        deck_path: Annotated[Optional[str], Field(
            description=DECK_PATH_DESC)] = None,
        net: Annotated[Optional[str], Field(
            description="볼 노드 이름. 사용자가 말했거나 다른 도구 결과에 "
                        "나온 이름만 넣는다.")] = None,
    ) -> dict[str, Any]:
        """노드 하나에 붙은 소자와 각 소자의 포트 번호를 알려줍니다.

        언제 사용하나:
        - "이 노드에 뭐가 붙어 있어?", "왜 3개야?" 처럼 노드 하나를 따라갈 때
        - 다른 도구가 보고한 노드를 자세히 볼 때

        사용자한테 받아야 하는 값:
        - deck_path(덱 경로)와 net(노드 이름)은 필수입니다.
          사용자가 말하지 않은 값이 있으면 호출하지 말고 먼저 물어보세요.

        사용하지 않는 경우:
        - 인스턴스 하나를 볼 때 (instance_info 를 쓰세요).

        출력 형식 (JSON):
        - status: "ok"
        - summary: 수행한 작업 한 줄 요약
        - net, element_count
        - elements: [{"element", "kind", "subckt", "ports", "where"}]

        사용자에게 답할 때:
        - 붙은 소자와 포트 번호를 목록으로 알려주세요.
        - 이름이 없다는 오류에 비슷한 이름이 나오면, 그중 어느 것인지 사용자에게 물어보세요.
        - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 필요한 값을 물어보세요.
        """
        src = require_user_path(deck_path, param="deck_path",
                                what="검사할 덱 파일", must_exist=True)
        if not net:
            raise ToolError("%s 볼 노드 이름(net)이 없습니다. 사용자에게 "
                            "물어보세요." % NEED_USER)
        r = _run(lambda: tools.net_info(src, opts, net))
        return _ok("노드 %s 에 소자 %d개" % (r["net"], r["element_count"]),
                   **r)

    @server.tool()
    def instance_info(
        deck_path: Annotated[Optional[str], Field(
            description=DECK_PATH_DESC)] = None,
        name: Annotated[Optional[str], Field(
            description="볼 인스턴스(소자) 이름. 예: XIO_DQ0. 사용자가 "
                        "말했거나 다른 도구 결과에 나온 이름만 넣는다.")] = None,
    ) -> dict[str, Any]:
        """인스턴스 하나가 무엇을 부르는지, 포트마다 어느 노드에 몇 개가 붙어 있는지 알려줍니다.

        언제 사용하나:
        - "XIO_DQ0 은 어디에 연결돼 있어?" 처럼 인스턴스 하나를 볼 때

        사용자한테 받아야 하는 값:
        - deck_path(덱 경로)와 name(인스턴스 이름)은 필수입니다.
          사용자가 말하지 않은 값이 있으면 호출하지 말고 먼저 물어보세요.

        사용하지 않는 경우:
        - 노드 하나를 볼 때 (net_info 를 쓰세요).

        출력 형식 (JSON):
        - status: "ok"
        - summary: 수행한 작업 한 줄 요약
        - element, kind, subckt, where
        - ports: [{"port", "net", "elements_on_net"}]

        사용자에게 답할 때:
        - elements_on_net 이 1 인 포트(혼자 매달린 노드)를 짚어 주세요.
        - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 필요한 값을 물어보세요.
        """
        src = require_user_path(deck_path, param="deck_path",
                                what="검사할 덱 파일", must_exist=True)
        if not name:
            raise ToolError("%s 볼 인스턴스 이름(name)이 없습니다. 사용자에게 "
                            "물어보세요." % NEED_USER)
        r = _run(lambda: tools.instance_info(src, opts, name))
        return _ok("%s (%s), 포트 %d개" % (r["element"], r["subckt"] or r["kind"],
                                          len(r["ports"])), **r)

    @server.tool()
    def save_lint_report(
        deck_path: Annotated[Optional[str], Field(
            description=DECK_PATH_DESC)] = None,
        output_path: Annotated[Optional[str], Field(
            description=OUTPUT_PATH_DESC)] = None,
        si: Annotated[bool, Field(
            description="true 면 SI 규칙 위반도 포함한다. SI 덱이라고 했을 "
                        "때만 true.")] = False,
        overwrite: Annotated[bool, Field(description=OVERWRITE_DESC)] = False,
    ) -> dict[str, Any]:
        """lint 전체 리포트를 txt 파일로 저장합니다 (CLI 의 lint -o 와 같은 내용).

        언제 사용하나:
        - lint 결과를 파일로 남겨 달라고 할 때

        사용자한테 받아야 하는 값:
        - deck_path(덱 경로)와 output_path(리포트 저장 경로)는 필수입니다.
          사용자가 말하지 않은 값이 있으면 호출하지 말고 먼저 물어보세요.

        사용하지 않는 경우:
        - 결과를 화면으로만 보고 싶을 때 (deck_summary, lint_findings 를 쓰세요).

        출력 형식 (JSON):
        - status: "ok"
        - summary: 수행한 작업 한 줄 요약
        - errors, warnings, notes: 등급별 개수
        - created / modified: 새로 만든 / 덮어쓴 파일의 절대경로 목록

        사용자에게 답할 때:
        - errors / warnings 개수와 저장된 리포트 경로를 함께 알려주세요.
        - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 필요한 값을 물어보세요.
        """
        src = require_user_path(deck_path, param="deck_path",
                                what="검사할 덱 파일", must_exist=True)
        dst = require_user_path(output_path, param="output_path",
                                what="리포트를 저장할 파일")
        r = _run(lambda: tools.lint_report(src, opts, si=si))
        created, modified = _run(
            lambda: tools.write_output(dst, r.pop("text"), overwrite))
        return _ok("lint 리포트 저장: error %d / warn %d"
                   % (r["errors"], r["warnings"]),
                   created=created, modified=modified, **r)

    @server.tool()
    def make_termination(
        deck_path: Annotated[Optional[str], Field(
            description=DECK_PATH_DESC)] = None,
        output_path: Annotated[Optional[str], Field(
            description=OUTPUT_PATH_DESC + " 보통 .inc 파일.")] = None,
        kind: Annotated[str, Field(
            description="종단 소자 종류: rload(저항) 또는 cload(커패시터). "
                        "사용자가 말하지 않았으면 rload.")] = "rload",
        value: Annotated[str, Field(
            description="저항값 (kind=rload 일 때). 예: 50, 1k, 1T")] = "1T",
        value2: Annotated[str, Field(
            description="커패시터값 (kind=cload 일 때). 예: 5f")] = "1f",
        keep_net: Annotated[Optional[list[str]], Field(
            description=KEEP_NET_DESC)] = None,
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
        - preview: 파일 앞부분 몇 줄
        - created / modified: 새로 만든 / 덮어쓴 파일의 절대경로 목록

        사용자에게 답할 때:
        - 몇 개를 만들었는지와 저장 경로를 알려주세요.
        - 반드시 내용을 검토한 뒤 덱에 .include 하라고 알려주세요
          (안 쓰는 핀인지 빠뜨린 결선인지 도구는 구분하지 못합니다).
        - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 필요한 값을 물어보세요.
        """
        src = require_user_path(deck_path, param="deck_path",
                                what="검사할 덱 파일", must_exist=True)
        dst = require_user_path(output_path, param="output_path",
                                what="종단 .inc 를 저장할 파일")
        r = _run(lambda: tools.make_termination(
            src, opts, kind=kind, value=value, value2=value2,
            keep_net=keep_net, exclude=exclude))
        text = r.pop("text")
        created, modified = _run(
            lambda: tools.write_output(dst, text, overwrite))
        body = [ln for ln in text.splitlines() if ln and not ln.startswith("*")]
        return _ok("종단 %d개를 %s 에 저장" % (r["count"], dst),
                   preview=body[:10], created=created, modified=modified,
                   **r)

    @server.tool()
    def render_graph(
        deck_path: Annotated[Optional[str], Field(
            description=DECK_PATH_DESC)] = None,
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
        - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 필요한 값을 물어보세요.
        """
        src = require_user_path(deck_path, param="deck_path",
                                what="그릴 덱 파일", must_exist=True)
        dst = require_user_path(output_path, param="output_path",
                                what="그림을 저장할 파일", suffix=".html")
        existed = os.path.exists(dst)
        if existed and not overwrite:
            raise ToolError("%s 파일이 이미 있습니다: %s. 덮어쓸지"
                            "(overwrite=true) 다른 경로를 쓸지 사용자에게 "
                            "물어보세요." % (NEED_USER, dst))
        argv = ["graph", src, "-o", dst, "--profile", opts.profile]
        for d in opts.search_dirs:
            argv += ["--search-dir", d]
        for p in opts.opaque[len(deck_mod.DEFAULT_OPAQUE):]:
            argv += ["--opaque", p]
        for p in opts.skip:
            argv += ["--skip", p]
        if opts.max_depth is not None:
            argv += ["--max-depth", str(opts.max_depth)]
        # cli prints its own progress; keep stdout for the protocol
        saved, sys.stdout = sys.stdout, sys.stderr
        try:
            code = cli_mod.main(argv)
        finally:
            sys.stdout = saved
        if code:
            raise ToolError("그림을 만들지 못했습니다 (exit %s)" % code)
        return _ok("연결 그림을 %s 에 저장" % dst,
                   created=[] if existed else [dst],
                   modified=[dst] if existed else [])

    return server


def main(argv=None):
    p = argparse.ArgumentParser(prog="primesim_dm.mcp_server",
                                description=__doc__.split("\n")[0])
    p.add_argument("--search-dir", action="append", default=[])
    p.add_argument("--opaque", action="append", default=[], metavar="REGEX")
    p.add_argument("--skip", action="append", default=[], metavar="REGEX")
    p.add_argument("--max-depth", type=int, metavar="N")
    p.add_argument("--profile", choices=sorted(deck_mod.PROFILES),
                   default=deck_mod.DEFAULT_PROFILE)
    p.add_argument("--keep-net", action="append", default=[], metavar="REGEX",
                   help="nets every tool leaves out, e.g. site power nets")
    args = p.parse_args(argv)
    opts = tools.ReadOptions(search_dirs=args.search_dir, opaque=args.opaque,
                             skip=args.skip, max_depth=args.max_depth,
                             profile=args.profile, keep_nets=args.keep_net)
    build(opts).run("stdio")


if __name__ == "__main__":
    main()
