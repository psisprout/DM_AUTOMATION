"""MCP 연결 테스트 (가이드 5.2절). 에이전트 없이 MCP 서버에 직접 붙어서 tool 목록과 호출 결과를 본다.

    python test_mcp.py 2>/dev/null                 # 샘플 파일을 만들어 기본 시나리오 실행
    python test_mcp.py /절대경로/내파일.csv 2>/dev/null   # 내 파일로 load → analyze → plot

mcp 1.x, 2.x 모두에서 동작. 작업공간은 이 테스트 동안만 임시 폴더를 쓴다.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
MCP_PATH = HERE / "MCP.py"


def scenario(sample_dir: Path, user_file=None):
    if user_file:
        return [
            ("load_current_profile", {"path": user_file, "name": "mine"}),
            ("analyze_current_profile", {"name": "mine"}),
            ("plot_current_profiles", {"names": ["mine"]}),
        ]
    return [
        # (tool 이름, 넘길 값) — 필수 값이 빠진 경우도 꼭 넣어본다
        ("load_current_profile", {}),
        ("load_current_profile", {"path": str(sample_dir / "basic_ms_mA.csv"), "name": "meas"}),
        ("load_current_profile", {"path": str(sample_dir / "test.sp")}),                     # 소스가 여러 개 → 되묻기
        ("load_current_profile", {"path": str(sample_dir / "test.sp"), "current_col": "Iload", "name": "sim"}),
        ("analyze_current_profile", {"name": "meas", "window": "100us"}),
        ("transform_current_profile", {"name": "sim", "time_scale": 2, "repeat": 3}),
        ("align_current_profiles", {"ref": "meas", "targets": ["sim"]}),
        ("combine_current_profiles", {"names": ["meas", "sim_aligned"], "weights": [1, -1], "outside": "crop"}),
        ("plot_current_profiles", {"names": ["meas", "sim_aligned"]}),
        ("export_current_profile", {"name": "meas"}),                                        # 경로 없음 → 되묻기
        ("list_current_profiles", {}),
    ]


async def main():
    user_file = sys.argv[1] if len(sys.argv) > 1 else None
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        sys.path.insert(0, str(HERE / "tests"))
        import samples
        samples.make_all(tmp / "samples")
        env = dict(os.environ, CP_WORKSPACE=str(tmp / "ws"))
        params = StdioServerParameters(command=sys.executable, args=[str(MCP_PATH)], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                print("tool 목록:", [t.name for t in tools.tools])
                for name, args in scenario(tmp / "samples", user_file):
                    result = await session.call_tool(name, args)
                    is_error = getattr(result, "is_error", getattr(result, "isError", None))
                    print(f"\n== {name} {args}\n오류 여부: {is_error}")
                    print(result.content[0].text[:1500])


if __name__ == "__main__":
    asyncio.run(main())
