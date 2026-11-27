"""MCP 서버를 실제로 띄워서 tool 목록/호출을 확인 (가이드 5.2절의 test_mcp.py와 같은 방식)."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

EXPECTED = {"load_current_profile", "list_current_profiles", "analyze_current_profile", "transform_current_profile",
            "combine_current_profiles", "align_current_profiles", "plot_current_profiles", "export_current_profile",
            "delete_current_profiles"}


async def _session(calls):
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "MCP.py")], env=dict(os.environ))
    out = []
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            out.append({t.name for t in tools.tools})
            for name, args in calls:
                res = await s.call_tool(name, args)
                err = getattr(res, "is_error", getattr(res, "isError", None))
                out.append((err, res.content[0].text))
    return out


def test_tools_and_calls(tmp_path):
    csv = tmp_path / "a.csv"
    csv.write_text("Time (us),Current (mA)\n" + "".join(f"{k},{(100 if 200 <= k < 500 else 1)}\n" for k in range(1000)))
    res = asyncio.run(_session([
        ("load_current_profile", {}),
        ("load_current_profile", {"path": "relative/a.csv"}),
        ("load_current_profile", {"path": str(csv), "name": "a"}),
        ("transform_current_profile", {"name": "a", "repeat": 2, "time_shift": "-100us"}),
        ("analyze_current_profile", {"name": "a", "t_start": "100us", "t_end": "600us"}),
        ("export_current_profile", {"name": "a"}),
        ("combine_current_profiles", {"names": ["a", "a"], "weights": [1, -0.5]}),
    ]))
    assert res[0] == EXPECTED
    err, txt = res[1]
    assert err and "[사용자 확인 필요]" in txt and "path" in txt
    err, txt = res[2]
    assert err and "절대경로" in txt
    err, txt = res[3]
    assert not err and json.loads(txt)["names"] == ["a"]
    err, txt = res[4]
    assert not err and json.loads(txt)["profile"]["t_start"] == "-100 us"
    err, txt = res[5]
    d = json.loads(txt)
    assert d["range"]["duration"] == "500 us" and d["basic"]["max"] == "100 mA"
    err, txt = res[6]
    assert err and "output_path" in txt
    err, txt = res[7]
    assert not err and json.loads(txt)["result_name"] == "a-0.5xa"
