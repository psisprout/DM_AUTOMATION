# DM_AUTOMATION

## chanxt - 채널 S-파라미터 IL / RL / NEXT / FEXT / Power-sum 크로스톡 분석

채널 모델(`.sNp`)을 읽어서 모든 net의 IL, RL, NEXT, FEXT, PSNEXT, PSFEXT를 계산합니다.
그리고 사용자가 정한 **시나리오(IN/OUT 방향)** 기준으로 victim 리시버에서의 power-sum
크로스톡과 aggressor별 기여도를 계산합니다. 보고 싶은 곡선만 골라서 HTML 그래프로 보거나,
특정 주파수 값을 CSV로 뽑을 수 있습니다.

```
.sNp (여러 모델) ─▶ 포트 → net + 위치 ─▶ 터미네이션 ─▶ 기본 지표 + 시나리오 지표
                                                          │
             HTML 그래프 / CSV ◀─ 선택 (필터 · 매트릭스 · 성분 분해) ◀┘
```

### 실행

```bash
python -m chanxt.gui                       # GUI
python -m chanxt.gui project.json          # 저장한 프로젝트 열기
python -m chanxt.cli project.json --html out.html --csv out.csv   # GUI 없이
```

필요한 것: **Python 3.8 이상**, `numpy`, Qt 바인딩 하나(PyQt5 / PyQt6 / PySide2 / PySide6).
BBS-SPARA 정합성 체크 툴(`sparabbs`)과 같은 구성입니다. HTML 그래프는 plotly.js를 툴 안에
포함해서 직접 생성하므로 **추가 pip 설치가 없고, 오프라인에서도** 열립니다. CLI는 Qt 없이 동작합니다.

---

### GUI 순서

**1 · Models & ports** - `.sNp` 파일을 여러 개 추가합니다. 각 모델의 alias(예: revA)가
그래프 범례에 쓰입니다.

* 포트 이름이 헤더에 있으면(`! Port[1] = DQ0_SoC` 등) **net과 위치(location)로 자동 분리**합니다.
  * `NET_LOC`: 마지막 토큰이 위치 (`DQ0_SoC`, `WCK0_t_DRAM`)
  * `LOC_NET`: 첫 토큰이 위치 (`U1_DQ0`)
  * Auto-detect가 둘 중 맞는 쪽을 고릅니다. 정규식을 직접 쓸 수도 있습니다(`(?P<net>…)`, `(?P<loc>…)`).
* **Location alias**(`U1=SoC, U2=DRAM`)로 refdes 같은 토큰을 읽기 좋은 이름으로 바꿉니다.
* 이름이 없는 파일은 위치에 따라 채웁니다.
  * *Ports 1..N/2 = A, rest = B* 또는 *Odd = A, even = B*
  * *Paste net names*: N개(포트별)나 N/2개(양 끝 공통)를 붙여넣습니다.
* 표의 Net / Location 칸은 직접 고칠 수 있습니다.

**2 · Nets & groups**

* **그룹 규칙**(정규식, 위에서부터 먼저 맞는 것)으로 net을 DQ, DM, RDQS, WCK, CA, CS, CK, POWER,
  UNUSED 등으로 나눕니다.
* **태그 규칙**(선택)으로 Byte0/Byte1 같은 묶음을 붙입니다.
* net 표에서 그룹이나 태그를 직접 바꾸면 그 net만 규칙보다 우선합니다(굵게 표시).
* 위치 순서(드래그)는 기본 IL 방향(앞 → 뒤)과 표 정렬 순서에 쓰입니다.

**3 · Terminations** - 분석하지 않는 포트를 무엇으로 끝낼지 정합니다.

| 종류 | 의미 |
|---|---|
| Signal | 분석 대상 포트 (기본값) |
| Z0 | 기준 임피던스로 종단 (포트 제거) |
| Short | GND에 단락 |
| Open | 개방 |
| R / L / C | 해당 소자로 종단 (값은 `40`, `1p`, `2.2n`, `1meg` 형식) |
| R + C 직렬, R ∥ C | RC 조합 |

* 우선순위: **시나리오 net > 시나리오 그룹 > 공통 net > 공통 그룹 > Signal**
* 같은 단계에서는 위치를 지정한 규칙이 `*`보다 이기고, 아래쪽 행이 이깁니다.
* 새 프로젝트는 POWER = Short, UNUSED = Open으로 시작합니다.
* 계산식은 `S' = S_KK + S_KT Γ (I − S_TT Γ)⁻¹ S_TK`입니다. C와 L은 주파수마다 Γ를 다시 계산합니다.

**4 · Scenarios & speed**

* **시나리오**(WRITE, READ, TX …, 이름과 개수 자유)마다 그룹별로 **IN(드라이버) 위치**와
  **OUT(리시버) 위치**를 정합니다. net 행으로 개별 override할 수 있습니다.
* **Quiet**로 지정한 net은 aggressor에서도 victim에서도 빠집니다.
* **OUT을 여러 개** 쓰면 multi-drop입니다(`DRAM0, DRAM1`).
* 시나리오별 **터미네이션 override**도 넣을 수 있습니다. 예를 들어 rank0에 WRITE할 때 rank1 쪽 포트를 Open이나 ODT R로 둡니다.
* **LPDDR5X preset**은 WRITE/READ 시나리오, 속도, 기본 터미네이션을 한 번에 채웁니다.
  * WRITE: SoC가 DQ, DM, CA, CS, CK, WCK를 구동하고, DRAM이 RDQS를 구동합니다.
  * READ: DRAM이 DQ, DM, RDQS를 구동하고, SoC가 CA, CS, CK, WCK를 구동합니다.
* **Speed**: 기본 Gbps를 하나 정하고 그룹별로 override합니다. Nyquist = Gbps / 2 (NRZ).
  * LP5X preset은 CA, CK = DQ/4, CS = DQ/8로 둡니다. 클럭의 Nyquist는 클럭 주파수 자체입니다.
* **Aggressor 범위**: 전체 net 또는 victim과 같은 태그만, 그리고 포함할 그룹을 고릅니다.
  모든 power sum에 적용됩니다.
* *Compute*로 계산합니다. 5/6번 탭을 열 때 설정이 바뀌었으면 자동으로 다시 계산합니다.

**5 · Select** - 곡선을 하나씩 고르지 않고 **조건으로** 고릅니다.

* 차원별 체크: 모델 × 시나리오(basic 포함) × 지표 × 관측 위치 × victim 그룹 × victim 태그,
  그리고 victim 이름(glob `DQ[0-3]`, `RDQS*` 또는 `re:정규식`).
* 개별 aggressor 곡선(NEXT, FEXT, 시나리오 XT)은 수가 많으므로 아래 방식으로 줄입니다.
  * victim마다 **최악 K개**
  * **dB 임계값** 이상
  * 전부
  * 없음(매트릭스에서만 선택)
  * aggressor 그룹과 이름 필터도 함께 걸 수 있습니다.
* **Preview**: 선택 결과입니다. 지표별로 묶고 그 안에서 Nyquist 최악값 순으로 정렬합니다(IL은 낮은 값이 나쁨).
  체크를 끄면 그 곡선이 빠집니다.
* **Crosstalk matrix**: victim(행) × aggressor(열) 히트맵입니다. 값은 victim Nyquist 기준 dB입니다.
  셀을 체크해 추가하고, 행/열 헤더 클릭으로 한 줄을 통째로 켜고 끕니다.
* **Scenario breakdown**: victim 하나의 시나리오 PSXT를 aggressor별(또는 그룹별) 값, power 기여율 %,
  누적 %로 보여줍니다. 상위 K개나 체크한 행을 그래프 선택에 추가할 수 있습니다.
* 선택 조건은 **프리셋**으로 프로젝트에 저장됩니다. 마지막 선택도 자동 저장됩니다.
* 고른 곡선은 모델과 무관한 키로 저장됩니다. 그래서 한 번 고르면 선택된 모든 모델에서 같은 곡선이 나와 겹쳐 그려집니다.

**6 · Output**

* **HTML 리포트**(파일 하나로 공유 가능)
  * *Separate graphs per*: 지표 / 모델 / victim / 위치 중 체크한 차원으로 그래프를 나누고, 나머지는 겹쳐 그립니다.
  * 같은 곡선은 같은 색이고, **모델은 선 스타일**(실선, 점선 …)로 구분합니다.
  * 페이지 안 검색창: 단어 AND 검색, 또는 `/정규식/`.
  * 모델과 지표 on/off, 열 수 / 높이, Nyquist 표시선을 조정할 수 있습니다.
  * 범례를 클릭하면 같은 곡선의 모든 모델이 함께 숨겨집니다.
  * 화면 밖 그래프는 그리지 않아서 그래프가 많아도 무겁지 않습니다.
* **값 추출**: 주파수를 여러 개 입력(`2.67, 5.33, 800MHz`)하고 *각 곡선의 Nyquist* 열을 넣을 수 있습니다.
  데이터 사이 값은 dB 곡선을 선형 보간하고, 범위 밖은 빈칸입니다. CSV는 Excel에서 바로 열립니다(UTF-8 BOM).

---

### 지표 정의

`@X`는 victim을 위치 X의 포트에서 본 값, `←`는 aggressor, `@Y`는 aggressor가 구동되는 위치입니다.

| 이름 | 정의 |
|---|---|
| `IL \| DQ3 SoC→DRAM` | S(DQ3@DRAM, DQ3@SoC) |
| `RL@SoC \| DQ3` | S(DQ3@SoC, DQ3@SoC) |
| `NEXT@SoC \| DQ3 ← DQ2` | S(DQ3@SoC, DQ2@SoC) |
| `FEXT@DRAM \| DQ3 ← DQ2@SoC` | S(DQ3@DRAM, DQ2@SoC) |
| `PSNEXT@SoC \| DQ3` | 10·log10 Σ\|NEXT\|² (범위 안 aggressor) |
| `PSFEXT@DRAM \| DQ3` | 10·log10 Σ\|FEXT\|² (위치가 3개 이상이면 `PSFEXT@DRAM1←SoC`처럼 구동 위치별) |
| `WRITE XT@DRAM \| DQ3 ← RDQS0_t (NEXT)` | S(DQ3_OUT, RDQS0_t_IN): aggressor IN이 victim OUT과 같은 위치면 NEXT, 아니면 FEXT |
| `WRITE PSXT@DRAM \| DQ3` | 시나리오 기여도 전체의 power sum |
| `WRITE PSNEXT@DRAM \| DQ3`, `WRITE PSFEXT@…` | 그중 NEXT 부분, FEXT 부분 |
| `WRITE PSXT@DRAM \| DQ3 ← [CA] (FEXT)` | aggressor 그룹 하나의 power sum |
| `WRITE IL \| DQ3 SoC→DRAM`, `WRITE RL@…` | 시나리오 터미네이션 기준 IN→OUT IL, IN/OUT 포트 RL |

기본 지표는 **양쪽(모든 위치) 기준**을 다 계산합니다. 차동 신호(`_t/_c`)는 각각 single-ended net으로 다룹니다.

### CLI

```bash
python -m chanxt.cli project.json --preset "Byte0 WRITE" --list
python -m chanxt.cli project.json --html out.html --split metric,model
python -m chanxt.cli project.json --csv vals.csv --freqs "2.67, 5.33 GHz"
python -m chanxt.cli project.json --model revB=/path/new_revB.s96p --html cmp.html
```

선택은 `--preset`, 없으면 GUI에서 마지막으로 쓴 선택, 그것도 없으면 기본 보기(IL / RL / PS*)를 씁니다.

### 프로젝트 파일 (JSON)

모델 경로(같은 폴더 아래면 상대 경로), 포트 매핑, 위치 alias와 순서, 그룹/태그 규칙과 수동 지정,
터미네이션, 시나리오, 속도, aggressor 범위, 선택 프리셋, 출력 옵션이 모두 들어갑니다.
같은 보드의 다른 버전 파일에는 *Add .sNp*로 모델만 추가하면 같은 설정이 그대로 적용됩니다.

### 구조

```
chanxt/
  touchstone.py   Touchstone v1/v2 읽기(큰 파일은 청크 단위 numpy 변환)/쓰기
  project.py      프로젝트 설정, 이름 → net/위치, 그룹·태그, 터미네이션·방향 해석, LP5X preset
  terminate.py    터미네이션 Γ와 포트 축소 (|S|²을 float32로 보관)
  analysis.py     곡선 카탈로그(Key), 곡선, Nyquist 값, 라벨
  select.py       필터, 최악 K / 임계값, 매트릭스, 성분 분해
  export.py       HTML 리포트, CSV
  workflow.py     모델 로드/매핑 (CLI와 GUI 공통)
  cli.py          GUI 없는 실행
  web/            report_template.html, plotly.min.js (v2.35.3, MIT)
  gui/            qtcompat.py (sparabbs와 동일), widgets.py, app.py
tests/
  make_fixtures.py  RLC 래더 + 결합 C로 만든 LP5X 유사 채널, multi-drop 채널
  test_engine.py    엔진 테스트 (터미네이션은 독립적인 nodal 해석과 비교)
  test_gui.py       offscreen Qt로 GUI 전체 흐름
  test_compat.py    Python 3.8에서 안 되는 문법/API 검사
```

### 테스트

```bash
python tests/make_fixtures.py
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .
sh tests/run_on_py38.sh        # Python 3.8 환경을 만들어 전체 실행
```

Python 3.8.20 + numpy 1.24.4 + PyQt5 5.15.11에서 검증했습니다.

### 알아둘 점

* 모든 지표가 크기(dB)라서, 터미네이션을 적용한 네트워크는 |S|²(float32)만 보관합니다. 위상은 보지 않습니다.
* Nyquist는 NRZ 기준(Gbps / 2)입니다. PAM4 등은 Gbps 칸에 등가 값을 넣어 주세요.
* 너무 작은 값은 −300 dB로 잘라서 표시합니다.
* 메모리: 원본 S(complex128)에 터미네이션 조합마다 |S|² 한 벌이 더 필요합니다.
  96포트 × 1000포인트 기준 원본이 약 150 MB이고, 시나리오 하나당 약 40 MB 이하입니다.
