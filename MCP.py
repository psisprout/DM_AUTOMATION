#!/usr/bin/env python3
"""
MCP.py — 사내 AI agent(opencode 등)가 scripts/ 의 스크립트를 tool로 쓰게 해 주는 MCP 서버.

구성 (MCP_tool_addition.md 가이드와 같은 구조)
  1. 설정
  2. 공통 함수 (run_script, require_user_path ...)  ← 기존 MCP.py가 있으면 이미 있는 부분
  3. TOOL 정의 — current profile(전류 파형)        ← 기존 MCP.py에 붙일 때는 이 구역만 복사
  4. 서버 실행

실행:
  stdio (opencode가 직접 실행):  python MCP.py
  HTTP  (서버를 따로 띄울 때):    python MCP.py --http --host 0.0.0.0 --port 8000

주의: 이 파일 안에서 print() 금지 (stdio에서는 stdout이 통신 채널). 로그는 log.info().
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import Field

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
    MCP_MAJOR = 2
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError
    MCP_MAJOR = 1


# ═════════════════════════════════════════════════════════════════════════════
# 1. 설정
# ═════════════════════════════════════════════════════════════════════════════
BASE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = BASE_DIR / "scripts"
SCRIPT_TIMEOUT_SEC = 60          # 스크립트 한 번 실행 제한 시간 (늘리려면 관리자와 상의)
NEEDS_USER = "[사용자 확인 필요]"

logging.basicConfig(stream=sys.stderr, level=os.environ.get("MCP_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mcp-tools")

SERVER_INSTRUCTIONS = """\
전류 파형(current profile) 도구 모음입니다.
- 먼저 load_current_profile로 파일을 불러와 이름을 붙입니다. 이후 모든 tool은 파일 경로가 아니라 그 '이름'으로 파형을 가리킵니다.
- transform/combine/align 결과는 새 이름으로 작업공간에 저장되고 원본은 바뀌지 않습니다. 결과에 나온 result_name을 다음 호출에 그대로 쓰세요.
- 이름이 기억나지 않으면 list_current_profiles로 확인합니다.
- 사용자 폴더에 파일로 저장하는 것은 export_current_profile(데이터)과 plot_current_profiles(그래프, 경로 지정 시)뿐입니다.
- 시간/전류 값은 사용자가 말한 그대로 '2ms', '10us', '5mA'처럼 단위를 붙여 넘기면 됩니다 (숫자만 쓰면 초, A).
- 결과의 warnings는 사용자에게 꼭 전달하고, '[사용자 확인 필요]' 오류를 받으면 그 내용을 설명한 뒤 필요한 값을 물어보세요.
"""

mcp = _Server("current-profile", instructions=SERVER_INSTRUCTIONS)


# ═════════════════════════════════════════════════════════════════════════════
# 2. 공통 함수
# ═════════════════════════════════════════════════════════════════════════════
OUTPUT_PATH_DESC = ("결과를 저장할 파일의 절대경로. 사용자가 직접 말한 경로만 넣는다. "
                    "사용자가 말하지 않았으면 지어내지 말고 비워둔 채 먼저 물어본다.")
OVERWRITE_DESC = "같은 이름의 파일이 이미 있을 때 덮어쓸지 여부. 사용자가 덮어쓰라고 명시적으로 허락했을 때만 true."


def require_user_path(value: Optional[str], param: str = "output_path", what: str = "결과를 저장할 파일") -> str:
    """사용자가 정해야 하는 경로 검사. 없거나 상대경로면 [사용자 확인 필요] 오류, 정상이면 절대경로 문자열."""
    if value is None or not str(value).strip():
        raise ToolError(f"{NEEDS_USER} {what}의 경로({param})가 없습니다. 사용자에게 전체 경로(절대경로)를 물어본 뒤 다시 호출하세요.")
    p = Path(str(value).strip()).expanduser()
    if not p.is_absolute():
        raise ToolError(f"{NEEDS_USER} {param}는 절대경로여야 합니다 (입력값: {value}). "
                        "사용자에게 '/'로 시작하는 전체 경로를 물어본 뒤 다시 호출하세요.")
    return str(p)


def _snapshot(dirs: Optional[list]) -> dict:
    snap = {}
    for d in dirs or []:
        try:
            for e in os.scandir(d):
                if e.is_file():
                    st = e.stat()
                    snap[os.path.abspath(e.path)] = (st.st_mtime_ns, st.st_size)
        except OSError:
            pass
    return snap


def run_script(script: str, args: list, watch_dirs: Optional[list] = None) -> dict[str, Any]:
    """scripts/의 스크립트를 실행하고 stdout 마지막 줄의 JSON을 돌려준다.

    - .py는 이 서버와 같은 파이썬, .sh는 bash, 그 외는 실행권한(#!) 필요
    - JSON status=error면 ToolError (needs_user_input=true면 [사용자 확인 필요] 붙임)
    - JSON이 없으면 watch_dirs 전후 비교로 created/modified를 추정
    """
    path = (SCRIPTS_DIR / script).resolve()
    if SCRIPTS_DIR.resolve() not in path.parents or not path.is_file():
        raise ToolError(f"스크립트를 찾을 수 없습니다: {script}")
    if path.suffix == ".py":
        cmd = [sys.executable, str(path)]
    elif path.suffix == ".sh":
        cmd = ["bash", str(path)]
    elif os.access(path, os.X_OK):
        cmd = [str(path)]
    else:
        raise ToolError(f"실행할 수 없는 스크립트입니다 (chmod +x 필요): {script}")
    cmd += [str(a) for a in args]
    before = _snapshot(watch_dirs)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", MPLBACKEND="Agg")
    log.info("run: %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, cwd=str(SCRIPTS_DIR), stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=SCRIPT_TIMEOUT_SEC, env=env)
    except subprocess.TimeoutExpired:
        raise ToolError(f"{script} 실행이 {SCRIPT_TIMEOUT_SEC}초를 넘어 중단했습니다. 파일이 너무 크거나 구간이 너무 길 수 있습니다. "
                        "구간을 줄이거나(t_start/t_end), 간격을 키워(resample_dt) 다시 시도하세요.")
    if proc.stderr.strip():
        log.info("stderr(%s): %s", script, proc.stderr.strip()[-2000:])
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    result = None
    if lines:
        try:
            result = json.loads(lines[-1])
        except json.JSONDecodeError:
            result = None
    if isinstance(result, dict) and "status" in result:
        if result.get("status") == "error":
            msg = result.get("error") or "스크립트가 실패했습니다."
            raise ToolError(f"{NEEDS_USER} {msg}" if result.get("needs_user_input") else msg)
        return result
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-15:]
        raise ToolError(f"{script} 실행 실패 (종료 코드 {proc.returncode}): " + " | ".join(tail))
    after = _snapshot(watch_dirs)
    created = sorted(p for p in after if p not in before)
    modified = sorted(p for p in after if p in before and after[p] != before[p])
    return {"status": "ok", "summary": f"{script} 실행 완료 (결과 JSON이 없어 폴더 변화로 추정함)",
            "created": created, "modified": modified, "stdout_tail": lines[-20:]}


def opt(flag: str, value: Any) -> list:
    """값이 있을 때만 '--flag=값' 한 개로 (음수 '-1ms'도 옵션으로 오해받지 않게 = 형식)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return []
    return [f"--{flag}={value}"]


# ═════════════════════════════════════════════════════════════════════════════
# 3. TOOL 정의 — current profile(전류 파형)
#    스크립트: scripts/current_profile.py (+ scripts/cplib/)
# ═════════════════════════════════════════════════════════════════════════════
CP_SCRIPT = "current_profile.py"
TIME_Q = "숫자=초(s), 또는 사용자가 말한 그대로 '2ms', '10us'처럼 단위 붙인 문자열."
CUR_Q = "숫자=A, 또는 '5mA'처럼 단위 붙인 문자열."
NAME_DESC = "작업공간의 파형 이름 (이전 결과의 name/result_name). 모르면 list_current_profiles로 확인."
WS_OVERWRITE_DESC = "작업공간에 같은 이름이 있을 때 덮어쓸지. 사용자가 그 이름으로 덮어쓰라고 했을 때만 true (원본 파일은 안 바뀜)."
RESULT_NAME_DESC = "결과의 작업공간 이름 (영문/숫자/한글, _ . + -). 비우면 자동. 사용자가 이름을 정했을 때만 넣는다."


def _need_name(v: Optional[str], param: str = "name") -> str:
    if v is None or not str(v).strip():
        raise ToolError(f"파형 이름({param})이 없습니다. list_current_profiles로 이름을 확인하고, 어느 파형인지 모호하면 사용자에게 물어본 뒤 다시 호출하세요.")
    return str(v).strip()


def _need_names(v: Optional[list], param: str = "names", min_n: int = 1) -> list:
    names = [str(x).strip() for x in (v or []) if str(x).strip()]
    if len(names) < min_n:
        raise ToolError(f"파형 이름 목록({param})이 {min_n}개 이상 필요합니다. list_current_profiles로 이름을 확인한 뒤 다시 호출하세요.")
    return names


@mcp.tool()
def load_current_profile(
    path: Annotated[Optional[str], Field(
        description="불러올 전류 파형 파일의 절대경로. 사용자가 직접 말한 경로만 넣는다.")] = None,
    name: Annotated[Optional[str], Field(
        description="작업공간에서 이 파형을 부를 이름 (예: 'idle', 'burst', 'meas1'). 비우면 파일 이름으로 자동 지정.")] = None,
    current_col: Annotated[Optional[Union[int, str]], Field(
        description="전류로 쓸 열의 이름 또는 번호(1부터, 결과의 columns 목록 번호). 'all'이면 전류로 보이는 열을 모두 각각 불러온다. "
                    "비우면 자동 선택하고, 고를 수 없으면 열 목록과 함께 되묻는 오류가 온다.")] = None,
    time_col: Annotated[Optional[Union[int, str]], Field(
        description="시간 열의 이름 또는 번호. 보통 비워둔다(자동). 'none'이면 시간 열을 쓰지 않고 dt/sample_rate로 시간을 만든다.")] = None,
    time_unit: Annotated[Optional[Literal["s", "ms", "us", "ns", "ps", "min", "h"]], Field(
        description="파일의 시간 값 단위. 파일에 단위 표기가 없고 사용자가 알려줬을 때만 넣는다.")] = None,
    current_unit: Annotated[Optional[Literal["A", "mA", "uA", "nA", "pA"]], Field(
        description="파일의 전류 값 단위. 파일에 단위 표기가 없거나 잘못됐고 사용자가 알려줬을 때만 넣는다. "
                    "전압(V)으로 표기된 열을 값 그대로 전류로 쓰고 싶을 때도 넣는다.")] = None,
    dt: Annotated[Optional[Union[float, str]], Field(
        description="시간 열이 없는 파일의 샘플 간격. " + TIME_Q)] = None,
    sample_rate: Annotated[Optional[Union[float, str]], Field(
        description="시간 열이 없는 파일의 샘플레이트. 숫자면 Hz, '1MHz', '2.5GS/s'처럼 써도 된다.")] = None,
    shunt_ohm: Annotated[Optional[Union[float, str]], Field(
        description="전압 열을 션트 저항으로 나눠 전류로 바꿀 때의 저항값. 숫자면 Ω, '10mohm', '0.1'처럼 쓴다.")] = None,
    block: Annotated[Optional[Union[int, str]], Field(
        description="파일에 데이터 묶음이 여러 개일 때(LTspice .step, 스코프 세그먼트 등) 고를 번호(1부터) 또는 'all'.")] = None,
    sheet: Annotated[Optional[Union[int, str]], Field(
        description="엑셀 파일의 시트 이름 또는 번호(1부터). 비우면 데이터가 있는 첫 시트.")] = None,
    overwrite: Annotated[bool, Field(description=WS_OVERWRITE_DESC)] = False,
) -> dict[str, Any]:
    """전류 파형 파일을 읽어 작업공간에 이름을 붙여 저장합니다. 다른 모든 current profile tool의 시작점입니다.

    언제 사용하나:
    - "이 파일 불러와줘", "/data/a.csv 전류 파형 열어줘", "이 csv랑 pwl 파일 비교하고 싶어" 처럼
      전류 파형(current profile) 파일을 처음 다룰 때. 다른 tool은 파일 경로가 아니라 여기서 붙인 이름을 쓴다.
    - 읽을 수 있는 양식: CSV/TSV/TXT(쉼표, 탭, 공백, 세미콜론+소수점쉼표, 앞쪽 메타데이터 줄), 스코프 CSV
      (Tektronix, Keysight, Rigol, LeCroy, PicoScope), Monsoon/Otii/Joulescope CSV, PWL 파일, SPICE 넷리스트의
      PWL 소스, LTspice .asc, SPICE raw(LTspice/ngspice/Xyce, binary/ASCII, .step), LTspice 'Export data' txt,
      ngspice wrdata, HSPICE CSDF, Cadence PSF ASCII / ViVA CSV, Tektronix .isf, LeCroy .trc, Keysight .bin,
      엑셀(.xlsx/.xls/.ods), JSON, .npy/.npz, MATLAB .mat, HDF5, Parquet, Feather, TDMS, 그리고 이들의 .gz.

    사용자에게 받아야 하는 값:
    - path(파일 절대경로)는 필수. 사용자가 말하지 않았으면 호출하지 말고 먼저 물어본다.
    - 단위/열/샘플 간격은 파일에서 자동으로 알아낸다. 알아낼 수 없으면 "[사용자 확인 필요]" 오류로
      무엇이 필요한지(열 목록, dt, 단위, block 등) 알려주므로 그때 사용자에게 물어보고 다시 호출한다.

    사용하지 않는 경우:
    - 이미 불러온 파형을 다시 보거나 계산할 때 (그때는 이름으로 analyze/transform 등을 바로 호출).
    - 전류 파형이 아닌 일반 문서/코드 파일.

    출력 형식 (JSON):
    - names: 작업공간에 저장된 이름 목록 (다음 tool 호출에 이 이름을 사용)
    - profiles: 각 파형의 points, t_start, t_end, duration, sampling, mean, max, min,
      current_column/current_unit/time_unit (어떤 열과 단위로 읽었는지)
    - file_format: 인식한 파일 양식, columns: 파일의 열 목록
    - warnings: 가정한 내용(단위 가정, 시간 보정 등). 비어 있지 않으면 반드시 사용자에게 알린다.

    사용자에게 답할 때:
    - 붙인 이름, 어떤 열을 어떤 단위로 읽었는지, 길이/평균/최대를 짧게 알려준다.
    - warnings가 있으면 그대로 전하고 맞는지 확인을 받는다 (특히 "단위를 A로 가정" 같은 내용).
    - "[사용자 확인 필요]" 오류를 받으면 그 내용을 설명하고 필요한 값을 물어본다.
    """
    src = require_user_path(path, param="path", what="불러올 전류 파형 파일")
    args = ["load", f"--path={src}", "--json"]
    for f, v in (("name", name), ("current-col", current_col), ("time-col", time_col), ("time-unit", time_unit),
                 ("current-unit", current_unit), ("dt", dt), ("sample-rate", sample_rate), ("shunt-ohm", shunt_ohm),
                 ("block", block), ("sheet", sheet)):
        args += opt(f, v)
    if overwrite:
        args.append("--overwrite")
    return run_script(CP_SCRIPT, args)


@mcp.tool()
def list_current_profiles() -> dict[str, Any]:
    """작업공간에 불러와 둔 전류 파형 목록(이름, 길이, 평균, 만든 과정)을 보여줍니다.

    언제 사용하나:
    - "지금 뭐 불러와 있어?", "파형 목록 보여줘" 같은 요청.
    - 다른 tool에 넣을 파형 이름이 기억나지 않거나 확실하지 않을 때 (호출 전에 확인용).

    사용자에게 받아야 하는 값:
    - 없음.

    사용하지 않는 경우:
    - 특정 파형의 자세한 통계가 필요할 때 (analyze_current_profile 사용).

    출력 형식 (JSON):
    - workspace: 작업공간 폴더, count: 파형 개수
    - profiles: [{name, points, duration, t_start, mean, max, made_by(최근 만든 과정), saved_at}]

    사용자에게 답할 때:
    - 이름과 길이/평균을 간단한 표로 보여준다.
    """
    return run_script(CP_SCRIPT, ["list", "--json"])


@mcp.tool()
def analyze_current_profile(
    name: Annotated[Optional[str], Field(description=NAME_DESC)] = None,
    t_start: Annotated[Optional[Union[float, str]], Field(
        description="분석 구간 시작. 비우면 처음부터. " + TIME_Q)] = None,
    t_end: Annotated[Optional[Union[float, str]], Field(
        description="분석 구간 끝. 비우면 끝까지. " + TIME_Q)] = None,
    threshold: Annotated[Optional[Union[float, str]], Field(
        description="active/idle을 나누는 전류 기준. 비우면 자동(idle 레벨과 active 레벨의 중간). "
                    + CUR_Q + " '30%'처럼 쓰면 idle~active 레벨 사이의 비율 지점.")] = None,
    min_duration: Annotated[Optional[Union[float, str]], Field(
        description="이보다 짧은 active 구간은 noise로 보고 idle로 처리. " + TIME_Q)] = None,
    merge_gap: Annotated[Optional[Union[float, str]], Field(
        description="active 구간 사이의 idle 틈이 이보다 짧으면 하나의 burst로 합침. " + TIME_Q)] = None,
    window: Annotated[Optional[Union[float, str]], Field(
        description="이동 구간 평균의 창 길이 (예: '1ms' → 임의의 1ms 구간 중 최대/최소 평균 전류). " + TIME_Q)] = None,
    supply_voltage: Annotated[Optional[Union[float, str]], Field(
        description="평균 전력/에너지를 계산할 공급 전압. 숫자면 V, '3.8V'처럼 써도 된다.")] = None,
    smooth_time: Annotated[Optional[Union[float, str]], Field(
        description="노이즈가 심할 때 이 길이의 이동평균으로 다듬은 뒤 계산 (최대값, di/dt, active/idle에 영향). " + TIME_Q)] = None,
) -> dict[str, Any]:
    """전류 파형의 평균, RMS, 최대/최소, 전하량, 최대 di/dt, active/idle 평균, burst 통계를 계산합니다.

    언제 사용하나:
    - "평균 전류 얼마야?", "피크 몇 mA야?", "active 평균이랑 idle 평균 알려줘", "duty 몇 %야?",
      "burst 몇 번 있어? 주기는?", "2ms~5ms 구간 평균", "1ms 최대 평균 전류", "di/dt 최대",
      "3.8V 기준 평균 전력/에너지", "전하량(mAh)" 같은 수치 확인 요청.
    - transform/combine/align을 한 뒤 결과를 확인할 때.

    사용자에게 받아야 하는 값:
    - name(어떤 파형인지). 방금 불러오거나 만든 파형이면 그 이름을 쓰고, 모호하면 물어본다.
    - 구간, threshold 등은 사용자가 말했을 때만 넣는다 (나머지는 자동).

    사용하지 않는 경우:
    - 파형을 바꾸거나 새로 만드는 요청 (transform/combine/align 사용).
    - 그래프로 보고 싶다는 요청 (plot_current_profiles 사용).

    출력 형식 (JSON):
    - range: 분석 구간, 점 개수, 샘플링 간격
    - basic: mean(시간가중 평균), rms, max/max_at, min/min_at, peak_to_peak, charge(C와 Ah),
      max_rise_didt/max_fall_didt (A/us, 시각)
    - active_idle: threshold(사용한 기준과 정한 방법), active_mean, idle_mean, active_ratio(duty),
      active_time, idle_time, bursts{count, duration_min/mean/max, period_mean, first_bursts[...]}, notes
    - window_average: window를 준 경우 그 길이의 최대/최소 평균과 시작 시각
    - power: supply_voltage를 준 경우 mean_power, energy

    사용자에게 답할 때:
    - 물어본 값을 먼저 답하고, 단위를 붙여 그대로 전한다 (값을 반올림하거나 바꾸지 않는다).
    - active/idle 값을 말할 때는 사용한 threshold와 정한 방법도 함께 알려준다.
    - notes가 있으면 함께 알린다.
    """
    nm = _need_name(name)
    args = ["analyze", f"--name={nm}", "--json"]
    for f, v in (("t-start", t_start), ("t-end", t_end), ("threshold", threshold), ("min-duration", min_duration),
                 ("merge-gap", merge_gap), ("window", window), ("supply-voltage", supply_voltage),
                 ("smooth", smooth_time)):
        args += opt(f, v)
    return run_script(CP_SCRIPT, args)


@mcp.tool()
def transform_current_profile(
    name: Annotated[Optional[str], Field(description=NAME_DESC)] = None,
    t_start: Annotated[Optional[Union[float, str]], Field(description="잘라낼 구간 시작. " + TIME_Q)] = None,
    t_end: Annotated[Optional[Union[float, str]], Field(description="잘라낼 구간 끝. " + TIME_Q)] = None,
    rezero: Annotated[bool, Field(description="시작 시간을 0초로 옮길지 (자르기 다음에 적용).")] = False,
    time_scale: Annotated[Optional[float], Field(
        gt=0, description="시간 배율. 2 = 모든 시각을 2배 (2배 길고 느리게), 0.5 = 절반 (2배 빠르게).")] = None,
    time_shift: Annotated[Optional[Union[float, str]], Field(
        description="시간 이동량 (+면 뒤로 늦춤, -면 앞으로). " + TIME_Q)] = None,
    current_scale: Annotated[Optional[float], Field(
        description="전류 배율. 1.2 = 20% 증가, -1 = 부호 반전.")] = None,
    current_offset: Annotated[Optional[Union[float, str]], Field(
        description="전류에 더할 값 (예: '2mA' = 모든 점에 2mA 더함, '-1mA'). " + CUR_Q)] = None,
    clip_negative: Annotated[bool, Field(description="음수 전류를 0으로 바꿀지.")] = False,
    smooth_time: Annotated[Optional[Union[float, str]], Field(
        description="이 길이의 이동평균으로 노이즈를 다듬음. " + TIME_Q)] = None,
    resample_dt: Annotated[Optional[Union[float, str]], Field(
        description="이 간격의 균일 격자로 다시 샘플링 (점을 줄이거나 시뮬레이터 입력용으로 균일하게). " + TIME_Q)] = None,
    repeat: Annotated[Optional[int], Field(
        ge=1, le=1_000_000, description="파형을 몇 번 이어서 반복할지 (3 = 전체 길이 3배).")] = None,
    repeat_period: Annotated[Optional[Union[float, str]], Field(
        description="반복 주기. 비우면 파형 길이만큼 바로 이어 붙임. 파형보다 길면 빈 구간을 repeat_fill로 채움. " + TIME_Q)] = None,
    repeat_fill: Annotated[Literal["hold", "zero"], Field(
        description="repeat_period가 파형보다 길 때 빈 구간: hold=마지막 값 유지, zero=0A.")] = "hold",
    result_name: Annotated[Optional[str], Field(description=RESULT_NAME_DESC)] = None,
    overwrite: Annotated[bool, Field(description=WS_OVERWRITE_DESC)] = False,
) -> dict[str, Any]:
    """전류 파형 하나를 변환해 새 이름으로 저장합니다: 구간 자르기, 시간 scale/이동, 전류 scale/offset, 반복, 재샘플링, 스무딩.

    언제 사용하나:
    - "시간을 2배로 늘려줘", "0.5배 빠르게", "전류 1.5배", "전류 2mA 올려줘", "10번 반복해줘",
      "10ms 주기로 5번 반복", "1ms~3ms만 잘라줘", "100us 뒤로 밀어줘", "1us 간격으로 리샘플링",
      "노이즈 좀 줄여줘", "음수는 0으로".
    - 여러 변환을 한 번에 줄 수 있고, 항상 이 순서로 적용된다:
      자르기(t_start/t_end) → rezero → time_scale → time_shift → current_scale → current_offset
      → clip_negative → smooth_time → resample_dt → repeat. 다른 순서가 필요하면 여러 번 나눠 호출한다.

    사용자에게 받아야 하는 값:
    - name(어떤 파형인지)과 바꿀 내용 하나 이상. 배율/횟수 등 사용자가 말한 값만 넣고 지어내지 않는다.

    사용하지 않는 경우:
    - 두 개 이상의 파형을 더하기/빼기/이어붙이기 (combine_current_profiles 사용).
    - 두 파형의 켜지는 시점을 맞추기 (align_current_profiles 사용).
    - 숫자 확인만 하는 요청 (analyze_current_profile 사용).

    출력 형식 (JSON):
    - result_name: 결과 파형 이름 (원본은 그대로), applied: 실제 적용한 변환 목록(순서대로)
    - profile: 결과의 points, t_start, t_end, duration, mean, max, min

    사용자에게 답할 때:
    - 적용한 변환(applied)과 결과 이름, 결과 길이/평균을 알려준다. 파일로 필요하면 export를 안내한다.
    """
    nm = _need_name(name)
    args = ["transform", f"--name={nm}", "--json"]
    for f, v in (("t-start", t_start), ("t-end", t_end), ("time-scale", time_scale), ("time-shift", time_shift),
                 ("current-scale", current_scale), ("current-offset", current_offset), ("smooth", smooth_time),
                 ("resample-dt", resample_dt), ("repeat", repeat), ("repeat-period", repeat_period),
                 ("repeat-fill", repeat_fill), ("result-name", result_name)):
        args += opt(f, v)
    if rezero:
        args.append("--rezero")
    if clip_negative:
        args.append("--clip-negative")
    if overwrite:
        args.append("--overwrite")
    return run_script(CP_SCRIPT, args)


@mcp.tool()
def combine_current_profiles(
    names: Annotated[Optional[list[str]], Field(
        description="합칠 파형 이름 목록 (순서 중요: 빼기와 이어붙이기는 이 순서대로). " + NAME_DESC)] = None,
    weights: Annotated[Optional[list[float]], Field(
        description="각 파형에 곱할 계수 (names와 같은 개수). 비우면 모두 1. 빼기 A-B는 [1, -1], A+0.5B는 [1, 0.5], 평균은 [0.5, 0.5].")] = None,
    mode: Annotated[Literal["sum", "concat"], Field(
        description="sum = 같은 시각끼리 더하기(가중합), concat = 시간 순서대로 이어 붙이기 (A가 끝난 뒤 B).")] = "sum",
    outside: Annotated[Literal["zero", "hold", "crop"], Field(
        description="sum에서 시간 범위가 다를 때: zero = 범위 밖은 0A로 보고 전체 범위 사용(기본), "
                    "hold = 범위 밖은 처음/마지막 값 유지, crop = 모든 파형이 겹치는 구간만.")] = "zero",
    dt: Annotated[Optional[Union[float, str]], Field(
        description="결과를 이 간격의 균일 격자로 재샘플링 (비우면 모든 점을 정확히 유지). " + TIME_Q)] = None,
    result_name: Annotated[Optional[str], Field(description=RESULT_NAME_DESC)] = None,
    overwrite: Annotated[bool, Field(description=WS_OVERWRITE_DESC)] = False,
) -> dict[str, Any]:
    """여러 전류 파형을 더하거나 빼거나(가중합), 시간 순서대로 이어 붙여 새 파형으로 저장합니다.

    언제 사용하나:
    - "idle이랑 burst 더해줘", "A에서 B 빼줘(차이 보기)", "두 레일 전류 합쳐줘", "A 0.5배 + B",
      "두 측정 평균 파형", "A 다음에 B 이어 붙여줘", "부팅 → idle → 동작 순서로 연결해줘".
    - 샘플 간격/길이가 서로 달라도 된다 (시간축을 자동으로 합침).

    사용자에게 받아야 하는 값:
    - names: 어떤 파형들인지 (2개 이상). 빼기는 어느 쪽에서 어느 쪽을 빼는지 불분명하면 물어본다.

    사용하지 않는 경우:
    - 파형 하나의 배율/반복/자르기 (transform_current_profile 사용).
    - 켜지는 시점을 먼저 맞춰야 하는 비교라면 align_current_profiles를 먼저 호출한 뒤 그 결과로 combine.

    출력 형식 (JSON):
    - result_name: 결과 이름, profile: 결과의 points, t_start, t_end, duration, mean, max, min
    - notes: 범위 밖 처리 방식 등 계산 가정

    사용자에게 답할 때:
    - 어떤 식으로 합쳤는지(예: idle + burst_ts2), 결과 이름, 평균/최대를 알려주고 notes의 가정을 함께 전한다.
    """
    nms = _need_names(names, min_n=1)
    args = ["combine", "--names", *nms, "--json", f"--mode={mode}", f"--outside={outside}"]
    if weights:
        args.append("--weights=" + ",".join(repr(float(w)) for w in weights))
    args += opt("dt", dt) + opt("result-name", result_name)
    if overwrite:
        args.append("--overwrite")
    return run_script(CP_SCRIPT, args)


@mcp.tool()
def align_current_profiles(
    ref: Annotated[Optional[str], Field(description="기준이 되는(움직이지 않는) 파형 이름. " + NAME_DESC)] = None,
    targets: Annotated[Optional[list[str]], Field(
        description="기준에 맞춰 시간 이동할 파형 이름 목록 (보통 1개).")] = None,
    method: Annotated[Literal["edge", "max_didt", "xcorr"], Field(
        description="edge = 전류가 크게 켜지는 edge(idle→active 중간 레벨 통과 시점, 노이즈에 강함, 기본), "
                    "max_didt = di/dt가 가장 큰 순간(깨끗한 시뮬레이션 파형에 적합), "
                    "xcorr = 파형 전체 모양이 가장 잘 겹치는 위치(cross-correlation).")] = "edge",
    direction: Annotated[Literal["rising", "falling"], Field(
        description="rising = 켜지는(증가) edge, falling = 꺼지는(감소) edge.")] = "rising",
    edge_index: Annotated[Optional[int], Field(
        description="edge가 여러 개일 때 몇 번째를 쓸지 (1 = 첫 번째, -1 = 마지막). 비우면 가장 가파른 edge.")] = None,
    threshold: Annotated[Optional[Union[float, str]], Field(
        description="edge 판단 전류 기준. 비우면 자동(idle과 active 레벨 중간). " + CUR_Q + " '30%'도 가능.")] = None,
    smooth_time: Annotated[Optional[Union[float, str]], Field(
        description="노이즈가 심한 측정 파형일 때 edge를 찾기 전에 쓸 이동평균 길이. " + TIME_Q)] = None,
    max_shift: Annotated[Optional[Union[float, str]], Field(
        description="허용할 최대 이동량 (이보다 크면 오류, xcorr는 탐색 범위). " + TIME_Q)] = None,
    suffix: Annotated[str, Field(max_length=30, description="결과 이름 = target 이름 + suffix.")] = "_aligned",
    overwrite: Annotated[bool, Field(description=WS_OVERWRITE_DESC)] = False,
) -> dict[str, Any]:
    """두(또는 여러) 전류 파형의 켜지는 시점(di/dt가 큰 edge)을 맞추도록 target을 시간 이동해 새 이름으로 저장합니다.

    언제 사용하나:
    - "측정이랑 시뮬레이션 파형 켜지는 부분 맞춰줘", "두 파형 edge 정렬", "di/dt 큰 지점 기준으로 맞춰서 비교",
      "트리거가 달라서 시간이 어긋났어" 같은 요청.
    - 정렬 후 차이를 보려면 결과로 combine_current_profiles(weights=[1,-1])나 plot_current_profiles를 이어서 호출.

    사용자에게 받아야 하는 값:
    - ref(기준)와 targets(움직일 파형). 어느 쪽이 기준인지 불분명하면 물어본다.

    사용하지 않는 경우:
    - 이동량을 사용자가 직접 정해 준 경우 (transform_current_profile의 time_shift 사용).

    출력 형식 (JSON):
    - ref_edge: 기준 파형에서 찾은 edge (time, 몇 번째/총 개수, edge_slope, threshold)
    - results: [{target, result_name, shift(적용한 이동량, +면 늦춤), target_edge{...}}]
    - xcorr일 때는 target_edge에 correlation(1에 가까울수록 잘 겹침)

    사용자에게 답할 때:
    - 각 target을 얼마나 이동했는지(shift)와 기준 edge 시각, 결과 이름을 알려준다.
    - 찾은 edge가 여러 개였다면 몇 번째를 썼는지도 알려 주고, 다른 edge를 원하면 edge_index로 바꿀 수 있다고 안내한다.
    """
    r = _need_name(ref, "ref")
    tg = _need_names(targets, "targets")
    args = ["align", f"--ref={r}", "--targets", *tg, "--json", f"--method={method}", f"--direction={direction}",
            f"--suffix={suffix}"]
    args += opt("edge-index", edge_index) + opt("threshold", threshold) + opt("smooth", smooth_time) + \
        opt("max-shift", max_shift)
    if overwrite:
        args.append("--overwrite")
    return run_script(CP_SCRIPT, args)


@mcp.tool()
def plot_current_profiles(
    names: Annotated[Optional[list[str]], Field(description="그릴 파형 이름 목록 (여러 개면 겹쳐 그림). " + NAME_DESC)] = None,
    t_start: Annotated[Optional[Union[float, str]], Field(description="그릴 구간 시작. " + TIME_Q)] = None,
    t_end: Annotated[Optional[Union[float, str]], Field(description="그릴 구간 끝. " + TIME_Q)] = None,
    separate: Annotated[bool, Field(description="true면 파형마다 위아래로 따로 그림 (기본은 한 그래프에 겹쳐 그림).")] = False,
    title: Annotated[Optional[str], Field(max_length=120, description="그래프 제목. 비우면 파형 이름들.")] = None,
    output_path: Annotated[Optional[str], Field(
        description="그래프를 저장할 .png/.svg/.pdf 절대경로. 사용자가 경로를 말했을 때만 넣는다. "
                    "비우면 작업공간의 plots 폴더에 자동 저장한다.")] = None,
    overwrite: Annotated[bool, Field(description=OVERWRITE_DESC)] = False,
) -> dict[str, Any]:
    """전류 파형을 그래프(PNG 등)로 그려 저장하고 그 경로를 돌려줍니다. 점이 많아도 spike가 사라지지 않게 그립니다.

    언제 사용하나:
    - "그려줘", "그래프로 보여줘", "두 파형 겹쳐서 보여줘", "정렬 결과 확인하고 싶어", "1ms~2ms 확대해서 보여줘".

    사용자에게 받아야 하는 값:
    - names(어떤 파형인지). 저장 경로는 선택 (말하지 않았으면 비워서 자동 저장).

    사용하지 않는 경우:
    - 숫자만 필요한 요청 (analyze_current_profile 사용).
    - 데이터 파일(csv/pwl)로 저장하는 요청 (export_current_profile 사용).

    출력 형식 (JSON):
    - plot_path: 저장된 그래프 파일의 절대경로, created/modified

    사용자에게 답할 때:
    - plot_path를 그대로 알려준다 (그림 내용을 추측해서 설명하지 않는다. 수치는 analyze로 확인).
    """
    nms = _need_names(names)
    args = ["plot", "--names", *nms, "--json"] + opt("t-start", t_start) + opt("t-end", t_end) + opt("title", title)
    watch = None
    if output_path is not None and str(output_path).strip():
        dst = require_user_path(output_path, what="그래프를 저장할 파일")
        args += opt("output", dst)
        watch = [str(Path(dst).parent)]
    if separate:
        args.append("--separate")
    if overwrite:
        args.append("--overwrite")
    return run_script(CP_SCRIPT, args, watch_dirs=watch)


@mcp.tool()
def export_current_profile(
    name: Annotated[Optional[str], Field(description=NAME_DESC)] = None,
    output_path: Annotated[Optional[str], Field(
        description=OUTPUT_PATH_DESC + " 확장자로 형식을 정한다: .csv, .txt(탭 구분), .pwl(SPICE PWL 파일), .npz.")] = None,
    format: Annotated[Literal["auto", "csv", "txt", "pwl", "npz"], Field(
        description="저장 형식. auto면 확장자로 정한다.")] = "auto",
    time_unit: Annotated[Literal["s", "ms", "us", "ns"], Field(
        description="csv/txt에 쓸 시간 단위 (pwl/npz는 항상 s).")] = "s",
    current_unit: Annotated[Literal["A", "mA", "uA", "nA"], Field(
        description="csv/txt에 쓸 전류 단위 (pwl/npz는 항상 A).")] = "A",
    include_history: Annotated[bool, Field(
        description="csv/txt 맨 앞에 '#' 주석으로 원본과 만든 과정을 기록할지. 다른 툴이 '#' 줄을 못 읽으면 false.")] = True,
    overwrite: Annotated[bool, Field(description=OVERWRITE_DESC)] = False,
) -> dict[str, Any]:
    """작업공간의 전류 파형을 사용자가 정한 경로에 csv / txt / PWL / npz 파일로 저장합니다.

    언제 사용하나:
    - "결과 csv로 저장해줘", "/data/out.pwl로 내보내줘(시뮬레이션 입력용)", "mA, ms 단위로 저장해줘".

    사용자에게 받아야 하는 값:
    - name(어떤 파형인지)과 output_path(저장할 절대경로)는 필수. 경로를 말하지 않았으면 호출하지 말고 먼저 물어본다.
    - 이미 있는 파일이면 덮어쓸지 다른 경로로 할지 사용자에게 물어본다 (overwrite는 허락받았을 때만 true).

    사용하지 않는 경우:
    - 그래프 이미지 저장 (plot_current_profiles 사용).
    - 작업공간 안에서 이름만 바꾸거나 복사 (transform의 result_name 사용).

    출력 형식 (JSON):
    - output_path: 저장한 파일 절대경로, format, points, created/modified

    사용자에게 답할 때:
    - 저장 경로, 형식, 단위, 점 개수를 알려준다.
    - "[사용자 확인 필요]" 오류(폴더 없음, 같은 이름 파일 존재 등)를 받으면 설명하고 다시 물어본다.
    """
    nm = _need_name(name)
    dst = require_user_path(output_path)
    args = ["export", f"--name={nm}", f"--output={dst}", f"--format={format}", f"--time-unit={time_unit}",
            f"--current-unit={current_unit}", "--json"]
    if not include_history:
        args.append("--no-history")
    if overwrite:
        args.append("--overwrite")
    return run_script(CP_SCRIPT, args, watch_dirs=[str(Path(dst).parent)])


@mcp.tool()
def delete_current_profiles(
    names: Annotated[Optional[list[str]], Field(description="작업공간에서 지울 파형 이름 목록.")] = None,
) -> dict[str, Any]:
    """작업공간에서 파형을 지웁니다 (작업공간의 사본만 지우며, 사용자 원본 파일은 건드리지 않습니다).

    언제 사용하나:
    - 사용자가 "이거 지워줘", "작업공간 정리해줘"라고 명시적으로 요청했을 때만.

    사용자에게 받아야 하는 값:
    - 지울 이름 목록. 사용자가 지목하지 않은 파형은 지우지 않는다. 여러 개를 지울 때는 목록을 보여주고 확인을 받는다.

    사용하지 않는 경우:
    - 사용자가 지우라고 하지 않았을 때 (임의 정리 금지).

    출력 형식 (JSON):
    - deleted: 지운 이름, missing: 없던 이름

    사용자에게 답할 때:
    - 지운 이름을 알려준다.
    """
    nms = _need_names(names)
    return run_script(CP_SCRIPT, ["delete", "--names", *nms, "--json"])


# ═════════════════════════════════════════════════════════════════════════════
# 4. 서버 실행
# ═════════════════════════════════════════════════════════════════════════════
def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="사내 MCP 서버")
    ap.add_argument("--http", action="store_true", help="stdio 대신 streamable-http로 실행")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    if not a.http:
        mcp.run()
        return
    log.info("HTTP 모드: http://%s:%d/mcp", a.host, a.port)
    if MCP_MAJOR >= 2:
        mcp.run("streamable-http", host=a.host, port=a.port)
    else:
        mcp.settings.host = a.host
        mcp.settings.port = a.port
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
