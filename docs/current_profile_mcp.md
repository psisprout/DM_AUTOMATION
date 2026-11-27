# current profile MCP — 전류 파형을 말로 다루는 도구

사내 AI agent(opencode)에게 "이 csv랑 pwl 불러서 시간 2배로 늘리고 더한 다음 평균 알려줘"처럼 말하면,
전류 파형(current profile)을 불러오고, 더하고, 빼고, 늘리고, 반복하고, 정렬하고, 통계를 내 주는 MCP tool 모음입니다.
`MCP_tool_addition.md` 가이드의 구조(MCP.py + scripts/)를 그대로 따릅니다.

---

## 1. 무엇을 할 수 있나

| 하고 싶은 말 | 쓰이는 tool |
|---|---|
| "/data/a.csv 불러와줘" (csv, pwl, raw, 스코프 파일, 엑셀 …) | `load_current_profile` |
| "지금 뭐 불러와 있어?" | `list_current_profiles` |
| "평균/피크/RMS/전하량", "active 평균, idle 평균, duty", "burst 몇 개, 주기", "1ms 구간 최대 평균", "3.8V 기준 전력" | `analyze_current_profile` |
| "시간 2배", "전류 1.5배", "+2mA", "10번 반복", "10ms 주기로 5번", "1~3ms만 잘라줘", "100us 밀어줘", "1us로 리샘플링", "노이즈 줄여줘" | `transform_current_profile` |
| "A+B", "A−B", "0.5A+B", "두 측정 평균", "A 다음에 B 이어 붙여" | `combine_current_profiles` |
| "두 파형 켜지는 부분(di/dt 큰 곳) 맞춰줘" | `align_current_profiles` |
| "그려줘", "겹쳐서 보여줘", "1~2ms 확대" | `plot_current_profiles` |
| "/data/out.csv로 저장", "pwl로 내보내줘" | `export_current_profile` |
| "이거 지워줘" | `delete_current_profiles` |

### 작업공간(workspace) 개념 — 꼭 알아둘 것

```
사용자 파일 ──load──▶ [작업공간: 이름으로 저장]  ──transform/combine/align──▶ [새 이름으로 저장]
                         idle, burst,                    burst_ts2, idle+burst_ts2,
                         meas, sim                       sim_aligned ...
                                    ──analyze/plot──▶ 숫자/그래프
                                    ──export──▶ 사용자 파일 (csv/txt/pwl/npz)
```

- 파형 데이터(수백만 점)는 LLM을 거치지 않습니다. LLM은 이름과 요약 숫자만 주고받습니다.
- 모든 연산은 **새 이름**을 만들고 원본은 그대로입니다. 실수해도 원본 이름으로 다시 하면 됩니다.
- 사용자에게 경로를 묻는 것은 **불러올 때**와 **파일로 저장할 때**뿐입니다. 중간 결과는 경로 없이 이름으로 처리됩니다.
- 작업공간 위치: 기본 `~/.current_profiles/` (환경변수 `CP_WORKSPACE`로 변경). 각 파형은 `이름.npz` 하나입니다.
  그래프는 `작업공간/plots/`에 저장됩니다. 세션이 끝나도 남아 있으므로 다음 날 이어서 쓸 수 있습니다.

---

## 2. 설치 (VWP Linux)

```bash
cd /원하는/위치
git clone <이 저장소> DM_AUTOMATION
cd DM_AUTOMATION
python3 --version                       # 3.10 이상 (mcp 패키지 요구사항)
python3 -m pip install --user -r requirements.txt
python3 -m pip install --user -r requirements-optional.txt   # 엑셀/mat/h5 등도 읽으려면
```

conda를 쓰면 그 환경의 python으로 설치하고, 아래 opencode 설정의 `command`에도 그 python의 절대경로를 넣으세요.

### 단독 확인 (가이드 3장, 5장)

```bash
python3 -c "import MCP" && echo OK                     # 문법/패키지 확인
python3 tests/samples.py /tmp/cp_samples                # 여러 양식의 샘플 파일 생성
cd scripts
python3 current_profile.py load --path=/tmp/cp_samples/basic_ms_mA.csv --name=meas
python3 current_profile.py analyze --name=meas --window=100us
python3 current_profile.py load --path=/tmp/cp_samples/test.sp --current-col=Iload --name=sim --json | tail -1
cd ..
python3 test_mcp.py 2>/dev/null                         # MCP 서버에 직접 붙어서 시나리오 실행
python3 test_mcp.py /절대경로/내파일.csv 2>/dev/null       # 내 파일로 확인
python3 -m pytest -q tests                              # 전체 자동 테스트 (pytest 필요)
```

---

## 3. opencode에 연결

opencode는 MCP 서버를 직접 실행하는 **stdio(local)** 방식으로 붙입니다. 사용자마다 자기 계정으로 서버가 뜨므로
작업공간도 사람마다 따로입니다(가이드 7장 7번의 공용 계정 문제 없음).

프로젝트 폴더의 `opencode.json` 또는 전역 설정 `~/.config/opencode/opencode.json`에 추가합니다
(예시: `opencode.example.json`).

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "current-profile": {
      "type": "local",
      "command": ["/절대경로/python3", "/절대경로/DM_AUTOMATION/MCP.py"],
      "enabled": true,
      "environment": { "CP_WORKSPACE": "/절대경로/작업공간폴더" }
    }
  }
}
```

- `command`의 python은 2장에서 패키지를 설치한 그 python이어야 합니다 (`which python3`로 확인).
- `CP_WORKSPACE`는 생략 가능 (기본 `~/.current_profiles`). 홈 용량이 작으면 큰 디스크로 지정하세요.
- 설정 키 이름은 opencode 버전에 따라 다를 수 있습니다. 사내 opencode 문서의 "MCP servers / local" 항목과 맞춰 보세요.
- `MCP.py`를 고친 뒤에는 opencode를 재시작해야 반영됩니다. `scripts/` 안의 코드만 고쳤으면 재시작이 필요 없습니다.
- 여러 명이 서버 하나를 공유해야 한다면 `python3 MCP.py --http --host 0.0.0.0 --port 8000`으로 띄울 수 있지만,
  모든 요청이 서버 계정 권한으로 실행되고 작업공간도 공유되므로 권장하지 않습니다.

### 기존 MCP.py에 합치고 싶다면

이 저장소의 `MCP.py`는 가이드의 공통 함수(`run_script`, `require_user_path`, `OUTPUT_PATH_DESC`, `OVERWRITE_DESC`,
`NEEDS_USER`)를 직접 구현해 둔 독립 서버입니다. 이미 쓰고 있는 사내 `MCP.py`에 넣으려면:

1. `scripts/current_profile.py`와 `scripts/cplib/` 폴더를 사내 `scripts/`에 복사
2. `MCP.py`의 **"3. TOOL 정의 — current profile"** 구역(`CP_SCRIPT = ...`부터 `delete_current_profiles`까지)을 복사
3. 그 구역이 쓰는 `opt()` 함수도 같이 복사 (`--옵션=값` 한 덩어리로 넘기는 3줄짜리 함수)

---

## 4. 대화 예시

```
나: /data/meas/boot_idle.csv 랑 /sim/load.pwl 불러와줘
AI: (load 2회) meas = boot_idle.csv의 'CH1' 열(A 단위, 메타데이터), 10 ms, 평균 3.2 mA …
    sim = load.pwl, 6점 PWL, 0~999 us …
나: sim을 시간 2배로 늘리고 5번 반복해서 meas에 더해줘
AI: (transform: time_scale=2, repeat=5 → sim_ts2_rep5) (combine: meas + sim_ts2_rep5)
    결과 'meas+sim_ts2_rep5': 평균 18.4 mA, 최대 152 mA …
나: active 평균이랑 idle 평균은?
AI: (analyze) active 평균 101 mA (비율 15 %), idle 평균 1.1 mA, 기준 50.6 mA(자동: idle/active 레벨 중간)
나: 측정 파형이랑 시뮬 파형 켜지는 부분 맞춰서 차이 보여줘
AI: (align ref=meas targets=[sim]) → sim_aligned (이동 +12.3 us)
    (combine meas − sim_aligned, outside=crop) → (plot)
나: 결과 /data/out/diff.csv로 mA 단위로 저장해줘
AI: (export) /data/out/diff.csv 저장 (시간 s, 전류 mA, 10,000점)
```

---

## 5. 읽을 수 있는 파일 양식

양식은 확장자와 파일 내용으로 자동 판별합니다. 열/단위/샘플 간격도 헤더와 메타데이터에서 자동으로 찾고,
못 찾으면 "[사용자 확인 필요]"로 무엇이 필요한지(열 목록, dt, 단위, block 등) 되묻습니다.

| 분류 | 양식 | 비고 |
|---|---|---|
| 일반 표 | CSV, TSV, TXT, DAT, 공백 정렬 표 | 쉼표/탭/세미콜론/공백/`\|` 자동 판별, 앞쪽 메타데이터 줄, `#`/`;`/`%`/`*` 주석, 따옴표 |
| | 유럽식 `;` + 소수점 쉼표 (`0,001;1,5`) | |
| | 단위 표기: `Time (ms)`, `I[mA]`, `Time/us`, `current_mA`, 단위만 있는 다음 줄 (`second, Ampere`, `(us), (mA)`) | |
| | 시간 열이 없는 1열 데이터 | `Sample interval`, `XInc`, `Increment`, `Sample Rate`, `fs` 메타데이터 또는 dt/sample_rate 지정 |
| | 인코딩 UTF-8 / UTF-8 BOM / UTF-16 / CP949(한글 엑셀 CSV) | |
| | `.gz` 압축본 | 모든 양식 공통 |
| 스코프/계측기 CSV | Tektronix (구형 5열 형식, MSO 신형), Keysight/Agilent, Rigol (`X,CH1,Start,Increment`), LeCroy, PicoScope, Monsoon, Otii, Joulescope | 세로/가로 단위 메타데이터 사용 |
| 시뮬레이터 텍스트 | LTspice "Export data as text" (`.step` 포함), ngspice `wrdata` / `print`, HSPICE `.print` 표(SI 접미사 `1.2m`, `10n`), Cadence ViVA CSV(`name X, name Y` 쌍) / VCSV, HSPICE CSDF, Cadence PSF ASCII | |
| PWL | PWL 파일(`.pwl`, `t v` 쌍, `+` 상대시간, SI 접미사), SPICE 넷리스트(`.sp .cir .net .scs …`)의 I/V PWL 소스, `PWL FILE=` 참조, LTspice 회로도 `.asc`의 PWL 값, `TD=`, `TIME_SCALE_FACTOR=`, `VALUE_SCALE_FACTOR=`, `REPEAT FOR n (...) ENDREPEAT` | |
| SPICE raw | ngspice / LTspice / Xyce / Spectre(nutmeg) `.raw` — binary, ASCII, LTspice UTF-16 헤더·float32, fastaccess, `.step`(시간이 0으로 돌아가는 지점에서 block 분리) | |
| 스코프 바이너리 | Tektronix `.isf`, LeCroy `.trc`, Keysight/Agilent `.bin` | |
| 기타 | 엑셀 `.xlsx/.xlsm/.xls/.ods` (데이터 있는 첫 시트, `sheet`로 지정), JSON (배열 dict / 레코드 목록), NumPy `.npy/.npz`, MATLAB `.mat`(v5, v7.3, struct), HDF5 `.h5`(`units` 속성), Parquet, Feather, NI `.tdms` | 선택 패키지 필요 (requirements-optional.txt) |
| 안 되는 것 | Cadence PSF binary, Tek/Rigol `.wfm`, Joulescope `.jls`, Keysight `.dlog`, ASAM MDF | 어떻게 export하면 되는지 안내 메시지가 나옵니다 |

**검증 수준 (중요):** 모든 양식은 `tests/samples.py`가 만든 파일로 "같은 파형이 나오는지" 자동 테스트합니다
(`python3 -m pytest tests`). 다만 스코프/시뮬레이터 양식의 샘플은 실제 장비·툴에서 나온 파일이 아니라 공개된 양식
설명을 바탕으로 만든 것입니다. 특히 **Tektronix .isf, LeCroy .trc, Keysight .bin, LTspice binary raw, PSF ASCII,
CSDF, VCSV**는 실제 파일로 한 번씩 확인해 주세요. 실제 파일에서 안 맞는 부분이 있으면 그 파일 앞부분을
`tests/samples.py`에 샘플로 추가하고 reader를 고치면 됩니다.

### 자동 판단 규칙

- **전류 열 고르기:** 단위가 A/mA/uA…이거나, 이름이 `I(...)`, `current`, `curr`, `ibat`, `idd`, `전류` 등이면 전류로 봅니다.
  후보가 하나면 자동, 여러 개면 열 목록을 보여주며 되묻습니다. `current_col='all'`이면 전류로 보이는 열을 각각 불러옵니다.
- **전압 열:** 선택한 열이 V 단위면 되묻습니다. 션트 측정이면 `shunt_ohm`(예: `'10mohm'`), 실제로 전류 값이면 `current_unit`.
- **단위를 모를 때:** 시간은 초, 전류는 A로 가정하고 `warnings`에 남깁니다. 에이전트는 이 경고를 사용자에게 전하게 되어 있습니다.
- **시간 정밀도:** 텍스트 자릿수가 부족해 균일 간격이 조금씩 흔들리면 정확한 균일 간격으로 보정하고 경고를 남깁니다.
- **같은 시각 두 점(계단):** 두 번째 점을 아주 조금(최소 간격의 1e-6배) 뒤로 밀어 계단을 유지합니다.

---

## 6. 계산 규칙 (결과를 해석할 때)

| 항목 | 규칙 |
|---|---|
| 샘플 사이 | 선형보간 (PWL과 같은 해석) |
| 평균, RMS, 전하량 | **시간가중** 적분 (비균일 간격, PWL에서도 정확). RMS는 선형 구간의 정확한 적분 |
| active / idle | threshold 이상 = active. 기본 threshold = 시간가중 5% 레벨(idle)과 95% 레벨(active)의 중간. 교차 시점은 보간으로 정확히 계산. `min_duration`, `merge_gap`으로 noise 정리 |
| 구간 최대 평균 (`window`) | 길이 w의 모든 구간 중 평균이 가장 큰/작은 구간 (배터리/PMIC 사양 확인용) |
| 더하기/빼기 | 두 파형의 모든 시간점을 합쳐서 계산(정보 손실 없음). 범위가 다르면 `outside`: zero(범위 밖 0 A, 기본) / hold / crop(공통 구간) |
| 반복 / 이어붙이기 | 측정·표 데이터(균일 간격)는 N×dt 주기(마지막 샘플 다음 칸에서 다음 조각 시작), PWL은 꺾은점 길이가 주기. `repeat_period`로 직접 지정 가능 |
| 시간 scale | 모든 시각에 곱함 (0초 기준). 시작을 0으로 하려면 `rezero` |
| 재샘플링 | 간격을 키우면 구간 평균(전하량 보존), 줄이면 선형보간 |
| edge 정렬 | `edge`: idle/active 중간 레벨을 히스테리시스(레벨 차의 10%)로 통과하는 시점, 여러 개면 가장 가파른 것(`edge_index`로 선택). `max_didt`: di/dt 최대 순간. `xcorr`: 전체 모양 상호상관 |
| 그래프 | 점이 많으면 구간별 최소/최대를 남겨서 spike가 사라지지 않게 그림 |

---

## 7. 제한과 주의사항

- **60초 제한** (가이드 `SCRIPT_TIMEOUT_SEC`): 1천만 점(320 MB) CSV 불러오기 약 6초, 분석 약 11초, CSV 저장 약 24초
  (테스트 머신 기준). 더 큰 파일은 먼저 구간을 자르거나(`t_start/t_end`) 재샘플링(`resample_dt`)하세요.
- **최대 점 개수**: 결과 하나당 2천만 점 (`CP_MAX_POINTS` 환경변수로 변경). 넘으면 줄이는 방법을 안내하며 되묻습니다.
- **메모리**: 1천만 점 CSV를 읽을 때 약 2 GB를 씁니다.
- **작업공간 용량**: 1천만 점 파형 하나가 약 160 MB입니다. 필요 없는 것은 "지워줘"로 정리하세요 (원본 파일은 안 지워집니다).
- **그래프 한글**: 축 이름은 영어입니다. 파형 이름에 한글을 쓰면 서버에 한글 폰트(Nanum 등)가 없을 때 네모로 보일 수 있습니다.
- 사용자 파일을 덮어쓰는 것은 `export`/`plot`에서 `overwrite=true`일 때뿐입니다.

---

## 8. 새 양식 추가하기

1. `tests/samples.py`의 `make_all()`에 그 양식의 작은 샘플을 만드는 코드를 추가 (기준 파형 `T`, `I` 사용)
2. `tests/test_formats.py`의 `SAMPLE_KEYS`에 이름 추가 → `python3 -m pytest tests/test_formats.py`로 실패 확인
3. 텍스트 표 계열이면 대부분 `formats.py`의 자동 판별로 이미 읽힙니다. 안 되면:
   - 메타데이터 키 이름이 다르면 `DT_KEYS` / `RATE_KEYS` / `T0_KEYS` / `VUNIT_KEYS`에 추가
   - 완전히 다른 양식이면 `read_xxx(src) -> Parsed` 함수를 만들고 `sniff()`와 `read_file()`에 등록
4. `MCP.py`는 고칠 필요가 없습니다 (`load_current_profile` docstring의 양식 목록만 갱신).

---

## 9. 문제 해결

| 증상 | 원인 / 해결 |
|---|---|
| opencode에 tool이 안 보임 | `python3 -c "import MCP"`로 확인. `command`의 python 경로가 패키지 설치한 python인지 확인. opencode 재시작 |
| "전류 열을 하나로 정할 수 없습니다" | 정상 동작. 에이전트가 열 목록을 보여주면 어느 열인지 답하면 됩니다 |
| 값이 1000배 다름 | 단위 표기가 없어서 A로 가정한 경우. 불러온 결과의 warnings 확인 후 `current_unit='mA'`로 다시 불러오기 |
| "데이터 묶음(block)이 N개" | LTspice `.step`, 스코프 세그먼트 등. 번호나 'all'을 지정 |
| "읽을 수 없습니다 … export해 주세요" | 지원하지 않는 바이너리. 안내대로 CSV로 export |
| 60초 초과 | 파일이 너무 큼. 구간을 잘라서/재샘플링해서 사용 |
| `ModuleNotFoundError` | MCP 서버를 실행하는 python에 패키지 설치 (`requirements*.txt`) |

---

## 10. 파일 구성

```
MCP.py                         MCP 서버 (공통 함수 + current profile tool 9개)
scripts/current_profile.py     CLI 진입점 (load/list/analyze/transform/combine/align/plot/export/delete)
scripts/cplib/common.py        단위·숫자 해석, 작업공간, JSON 출력
scripts/cplib/formats.py       파일 양식 판별·읽기, 열/단위 선택
scripts/cplib/ops.py           파형 연산과 통계
scripts/cplib/plot.py          그래프
tests/                         자동 테스트 (samples.py: 샘플 파일 생성기)
test_mcp.py                    MCP 연결 수동 테스트 (가이드 5.2절)
opencode.example.json          opencode 설정 예시
```
