# primesim-dm 사용 가이드 (처음 쓰는 사람용)

PrimeSim / HSPICE 덱(`.sp`)을 **읽기만 해서** 결선이 맞는지 확인하는 도구입니다.
덱을 고치거나 시뮬레이션을 돌리지 않습니다.

| 명령 | 한 줄 설명 | 언제 쓰나 |
|---|---|---|
| `lint --si` | signal 노드마다 **소자가 몇 개 붙었는지** 세서, 2개가 아닌 곳을 알려줌 | 덱을 받았을 때 / 고친 뒤 제일 먼저 |
| `graph` | 같은 연결을 **그림(HTML)** 으로 보여줌 | lint가 잡은 곳을 눈으로 확인할 때 |
| `terminate` | 한쪽만 연결된 노드에 붙일 **종단 저항 줄**을 만들어 줌 | 안 쓰는 핀을 정리할 때 |

> 더 자세한 동작(검사 규칙, 읽는 범위 조절, 레이아웃 저장 등)은 맨 아래
> [부록](#부록)에 모아 두었습니다. 처음에는 1~4장만 보면 됩니다.

---

## 1. 준비

Python 3(3.6 이상)만 있으면 됩니다. 설치할 패키지는 없습니다.
`primesim-dm/` 폴더를 통째로 복사해 두고 실행합니다.

```bash
# 리눅스: 어느 폴더에서든
/path/to/primesim-dm/primesim-dm lint my_deck.sp --si
```

```cmd
:: 윈도우: 어느 폴더에서든
C:\tools\primesim-dm\primesim-dm.bat lint C:\sim\my_deck.sp --si
```

> 이 문서의 예시는 짧게 `primesim-dm ...` 으로 적었습니다. 위 경로로 바꿔서 쓰세요.
> 윈도우에서 `py --version` 이 안 되면 Python부터 설치하세요
> (설치 화면의 **"Add python.exe to PATH"** 체크).

---

## 2. `lint --si` — 노드에 붙은 소자 수 세기

### 기본 사용법

```bash
primesim-dm lint deck.sp --si
```

SI 덱에서 signal 노드는 보통 **드라이버 1개 + 리시버(또는 채널) 1개 = 2개**가 붙습니다.
`--si` 를 주면 노드마다 붙은 소자 수를 세서 2개가 아닌 노드를 전부 알려줍니다.

| 붙은 소자 수 | 뜻 | 흔한 원인 |
|---|---|---|
| **1개** | 어딘가 안 이어짐 | 노드 이름 오타, 결선 누락, 안 쓰는 핀 |
| **2개** | 정상 | |
| **3개 이상** | 의도치 않은 분기(stub) | 같은 노드 이름을 두 번 씀, 복붙 실수 |

### 결과 읽는 법

```
primesim-dm deck check
==================================================================
files    : 1
elements : 5 at the top level        ← 읽은 소자 수
nets     : 6                         ← 읽은 노드 수

by kind:                             ← 종류별 개수 요약
  ERROR si-node-fanout           3
  WARN  floating-net             2

ERROR si-node-fanout  net txp joins 3 element(s) (XTX, XCH, XSTUB); an SI node should join exactly 2  [deck.sp:11]
ERROR si-node-fanout  net en_rx joins 1 element(s) (XRX); an SI node should join exactly 2  [deck.sp:14]
WARN  floating-net    net en_rx is touched only by XRX (port 1)  [deck.sp:14]
...

3 error(s), 2 warning(s), 0 note(s)
```

한 줄씩 이렇게 읽으면 됩니다.

```
ERROR si-node-fanout  net txp  joins 3 element(s) (XTX, XCH, XSTUB) ... [deck.sp:11]
                          ^^^        ^                ^^^^^^^^^^^^^^       ^^^^^^^^^^
                          노드 이름  붙은 소자 수     붙은 소자 이름       덱 파일:줄번호
```

- 위 예에서 `txp` 에 `XSTUB` 이 하나 더 붙어서 3개 → 의도한 건지 확인
- `en_rx` 는 `XRX` 하나만 붙음 → 결선이 빠졌거나 안 쓰는 핀
- `floating-net` 경고는 "1개만 붙은 노드" 목록이고, `--si` 없이도 항상 나옵니다

**ERROR가 하나라도 있으면 종료 코드가 1**입니다(스크립트에서 바로 판별 가능).

### 전원 노드가 같이 잡힐 때

`vdd` 처럼 여러 소자에 물리는 전원 노드는 원래 3개 이상이 정상입니다.
`0` / `gnd` 와 `.global` 로 선언된 노드는 자동으로 빠지지만, 그 외 전원은
`--keep-net` 으로 빼 주세요(정규식, 여러 번 가능).

```bash
primesim-dm lint deck.sp --si --keep-net '^vdd' --keep-net '^vss'
```

### 자주 쓰는 옵션

| 옵션 | 하는 일 |
|---|---|
| `--keep-net REGEX` | 이 노드는 검사에서 뺌 (전원, 덱 입출력 끝단 등) |
| `--si-expect N` | 기대 소자 수를 2 대신 N으로 |
| `--si-ignore-passives` | R/C/L 은 안 셈 (드라이버+리시버+종단 저항을 2개로 보고 싶을 때) |
| `--summary` | 종류별 개수만 출력 |
| `--all` | 잘리지 않게 전부 출력 (기본은 종류마다 10줄) |
| `-o report.txt` | 결과를 파일로 저장 (화면이 잘려도 파일엔 전부 들어감) |
| `--search-dir DIR` | `.include` 파일을 못 찾을 때 찾아볼 폴더 추가 |

---

## 3. `graph` — 연결을 그림으로 보기

### 기본 사용법

```bash
primesim-dm graph deck.sp -o deck.html
```

만들어진 `deck.html` 을 **더블클릭해서 브라우저로 엽니다**(Chrome / Edge / Firefox).
인터넷 연결 없이 파일 하나로 동작합니다.

### 화면 보는 법

| 보이는 것 | 뜻 |
|---|---|
| 회색 박스 | 소자(인스턴스) |
| 흰 알약 | 노드(net). 굵은 선이면 버스 묶음 (`dq[0:7] x8`) |
| **빨간 점선 + 빨간 알약** | **한쪽만 연결된 노드** — lint의 `floating-net` 과 같은 것 |
| 노란 알약 + `·R` / `·C` | R/C 로 종단된 노드 (문제 아님) |
| 박스 밑 `⏚ vdd, vss` | 그 소자에 물린 전원 (선으로 안 그림) |

### 기본 조작

| 조작 | 하는 일 |
|---|---|
| 마우스 휠 | 확대 / 축소 |
| 드래그 | 화면 이동 |
| `fit` (또는 `0` 키) | 전체가 보이게 맞춤 |
| **노드 / 박스 클릭** | 그것과 연결된 것만 남기고 나머지는 흐리게 → 한 경로 따라가기 |
| `Esc` | 강조 해제 |

노드를 클릭하면 왼쪽 아래에 **붙은 소자 목록과 개수**가 뜹니다.
"이건 왜 빨간색이 아니지?"가 궁금할 때 보면 됩니다.

### 검색 (search)

툴바의 검색창에 이름 일부를 치면 맞는 노드·인스턴스만 표시됩니다.

```
XIO              이름에 XIO 가 들어간 것 (대소문자 무시)
dq3              버스로 묶여 있어도 원래 노드 이름으로 찾아짐
/^XIO_DQ[0-3]$/  슬래시로 감싸면 정규식
```

### 선택 (select) · leaf nets — `edit layout` 안에 있습니다

툴바의 **`edit layout`** 을 누르면 아래 버튼들이 나타납니다.

| 조작 / 버튼 | 하는 일 |
|---|---|
| 박스 클릭 | 하나 선택 |
| Ctrl(⌘) + 클릭 | 선택에 추가 / 빼기 |
| Shift + 빈 곳 드래그 | 사각형으로 여러 개 선택 |
| **더블클릭** / `siblings` | 이름이 비슷한 형제 전부 선택 (`XIO_DQ0` → `XIO_DQ0`~`7`) |
| **`select found`** | 검색창에 걸린 인스턴스 전부 선택 |
| `hide` | 선택한 것을 **그림에서만** 숨김 (덱은 그대로) |
| **`leaf nets`** | 선택한 인스턴스에**만** 매달린 잔가지 노드를 숨김 / 다시 보임 |
| `hidden…` / `show all` | 숨긴 것 목록에서 골라 되살리기 / 전부 되살리기 |

자주 쓰는 흐름:

1. 검색창에 `XIO_DQ` 입력 → `edit layout` → `select found` 로 한 번에 선택
2. `leaf nets` 로 잔가지를 접어서 큰 경로만 보기
3. 빨간 노드는 `leaf nets` 를 눌러도 **절대 숨겨지지 않습니다**(찾던 문제라서)

> 숨기기는 그림에만 적용됩니다. 빨간색(한쪽만 연결) 판정은 항상 덱 전체 기준이라,
> 무언가를 숨겼다고 다른 노드가 빨갛게 바뀌지 않습니다.

### 출력 형식

`-o` 확장자로 정해집니다.

| 확장자 | 용도 |
|---|---|
| `.html` | 화면에서 확대·검색·클릭하며 볼 때 (기본 추천) |
| `.svg` | 보고서에 붙일 정적 그림 |
| `.dot` | graphviz 로 넘길 때 (아주 큰 덱) |

---

## 4. `terminate` — 한쪽만 연결된 노드 종단하기

```bash
primesim-dm terminate deck.sp -o term.inc
```

lint에서 `floating-net` 으로 나온 노드마다 종단 저항 한 줄씩 만들어 **별도 파일**로 저장합니다.
덱 자체는 건드리지 않습니다.

```spice
Rterm_1   en_rx      0   1T   $ XRX port 1 (deck.sp:14)
Rterm_2   stub_end   0   1T   $ XSTUB port 1 (deck.sp:13)
```

각 줄 끝에 **어느 인스턴스의 몇 번 포트에서 나왔는지** 적혀 있습니다.
한쪽만 연결된 노드는 "안 쓰는 핀"일 수도, "누가 빠뜨린 결선"일 수도 있으니
**목록을 확인한 다음에** 덱에 `.include 'term.inc'` 를 넣으세요.

| 자주 쓰는 옵션 | 기본 | 설명 |
|---|---|---|
| `--nodes-only` | | 소자는 안 만들고 노드 목록만 보기 |
| `--type rload\|cload\|rc` | `rload` | 저항 / 커패시터 / 둘 다 |
| `--value` | `1T` | 저항값 |
| `--to` | `0` | 종단이 돌아갈 노드 |
| `--exclude REGEX` | | 이 노드는 종단하지 않음 |

---

## 5. 문제가 생기면

| 증상 | 해결 |
|---|---|
| `missing-include` 에러 | include 경로가 안 맞음. `--search-dir 폴더` 추가 |
| `checks-skipped` 경고, 연결 검사가 안 돎 | 못 읽은 include가 있어서 일부러 멈춘 것. include부터 해결 |
| 전원 노드가 `si-node-fanout` 으로 잔뜩 나옴 | `--keep-net '^vdd'` 등으로 제외 ([2장](#전원-노드가-같이-잡힐-때)) |
| 너무 오래 걸림 / 파일이 수천 개 | 모델·PDK 안쪽을 안 읽게 `--opaque` / `--skip` ([부록 C](#c-읽는-범위-줄이기)) |
| `off-profile-element` 경고 | DM 덱에 잘 안 쓰는 소자(M, D 등)가 있음. 읽게 하려면 `--profile full` |
| 그림에 박스가 너무 많음 | `--hide '^V'`, `--only 'XIO_'` 로 그릴 것만 고르기 |

---

# 부록

처음 쓸 때는 몰라도 되는 세부 동작입니다.

## A. lint 검사 항목 전체

| 코드 | 등급 | 내용 |
|---|---|---|
| `missing-include` | ERROR | `.include` / `.lib` 경로에 파일이 없음 |
| `undefined-subckt` | ERROR | X 소자가 부르는 subckt 정의가 어디에도 없음 |
| `port-count` | ERROR | X의 노드 개수 ≠ `.subckt` 포트 개수 |
| `duplicate-name` | ERROR | 같은 이름의 소자가 두 번 |
| `isolated-instance` | ERROR | 인스턴스의 모든 노드가 다른 데 안 붙음 |
| `missing-model-file` | ERROR | IBIS(`file=`) / Touchstone(`tstonefile=`) 파일이 없음 |
| `sparam-ports` | ERROR | S 소자의 노드 수가 `.sNp` 포트 수와 다름 |
| `si-node-fanout` | ERROR | `--si` 일 때만. 노드에 붙은 소자 수가 기대값과 다름 |
| `floating-net` | WARN | 소자 하나만 붙은 노드 |
| `unparsed-line` | WARN | 노드를 확정하지 못한 줄 (검사 범위 밖이라고 표시) |
| `off-profile-element` | WARN | 지금 프로필이 읽지 않는 소자 종류 |
| `ibis-incomplete` | WARN | B 소자에 `file=` / `model=` / `buffer=` 누락 |
| `improper-pattern` / `improper-option` | WARN | 기준 패턴·옵션 파일과 내용이 같은 파일이 덱에 없음 |
| `checks-skipped` | WARN | 못 읽은 include가 있어 연결 검사를 건너뜀 |
| `merged-net` | INFO | 0옴 저항이나 `.connect` 로 두 노드가 사실상 하나 (`-v` 로 보임) |

`--strict` 를 주면 WARN 에도 종료 코드 1을 냅니다.

## B. 노드 수를 세는 규칙

- **서로 다른 소자 수**로 셉니다. 한 인스턴스가 같은 노드를 두 포트에 물리면 1개입니다.
  (`X_RDL a a b c rdl` 의 `a` 는 1개 → `floating-net`, 메시지에 `at ports 0, 1` 표시)
- 노드 이름은 대소문자를 구분하지 않습니다 (`PAD_DQ0` = `pad_dq0`).
- `0`, `gnd`, `.global` 로 선언된 노드, `--keep-net` 에 걸린 노드는 세지 않습니다.
- **최상위만 봅니다.** `.subckt` 안쪽은 읽지 않고 포트 목록(인터페이스)만 씁니다.
- 소자 종류별 노드 위치:

  | 소자 | 노드로 보는 부분 |
  |---|---|
  | `X` | 마지막 이름(subckt 이름) 앞까지, `k=v` 제외 |
  | `S`, `B` | 첫 `k=v` 앞까지 |
  | `W` | `N=` 을 읽어 `2*(N+1)` 개 |
  | `R` `C` `L` `V` `I` | 2개 |
  | `K` | 없음 |

- 줄 이음: 다음 줄 맨 앞의 `+`, 그리고 `'...'` 따옴표가 안 닫힌 채 끝난 줄은
  다음 줄과 한 문장으로 붙입니다. 단 `.` 으로 시작하는 줄은 항상 새 문장입니다.
- `$` 뒤는 주석, `*` 로 시작하는 줄은 주석입니다.
- `.lib 'file' tt` 는 `tt` 섹션만 읽습니다. `.if` 분기는 판단하지 않고 전부 읽습니다.
- 확신이 안 서는 줄은 추측하지 않고 `unparsed` 로 빼서 개수와 줄번호를 보여줍니다.
- **못 읽은 include가 하나라도 있으면** `floating-net` / `isolated-instance` 검사를 하지
  않습니다. 그 파일 안 소자가 빠져서 수백 개의 가짜 경고가 나오는 걸 막기 위해서입니다.
  그래도 보려면 `--force-connectivity`.

## C. 읽는 범위 줄이기

IO 모델 밑에는 SPF·PDK가 수천 파일 딸려 오지만, 결선 검사에 필요한 건 포트뿐입니다.
세 명령(`lint` / `graph` / `terminate`)에 똑같이 쓸 수 있습니다.

| 옵션 | 동작 | 쓸 곳 |
|---|---|---|
| `--opaque REGEX` | `.subckt` 포트만 읽고 안쪽과 그 밑 include는 안 봄 | IO 모델, SPF (**먼저 이걸로**) |
| `--skip REGEX` | 파일을 아예 열지 않음 | subckt를 안 쓰는 PDK 라이브러리 |
| `--max-depth N` | include를 N단계까지만 따라감 | 뭐가 물려 있는지 모를 때 |
| `--profile full` | M, D, Q 등 모든 소자를 읽음 (기본 `dm`) | 트랜지스터가 최상위에 있는 덱 |

```bash
primesim-dm lint top.sp --si --opaque '/io_model/' --skip '/pdk/'
```

- 패턴은 **전체 경로**에 거는 정규식입니다. `/pdk/` 처럼 슬래시를 넣어야 엉뚱한 폴더에 안 걸립니다.
- `.spf` / `.dspf` / `.spef` / `.rcx` 는 기본으로 opaque 입니다 (`--no-default-opaque` 로 해제).
- 무엇을 안 읽었는지는 결과 헤더에 항상 찍힙니다.

include 상대경로를 찾는 순서: ① include를 적은 파일의 폴더 → ② 최상위 덱 폴더 →
③ 현재 폴더 → ④ `--search-dir`. 환경변수(`$MODELS/...`)도 풀어 줍니다.

## D. graph 세부 기능

**그림을 단순하게 만드는 규칙**

- 전원(`0`/`gnd`/`vss`/`vdd`/`vcc`/`avdd`/`avss`/`vbb`/`vpp`)은 선 대신 박스 밑에 글자로.
  프로젝트 전용 전원은 `--rail '^vddca'`, 끄려면 `--no-rails`.
- `dq0`~`dq7` 처럼 **이름 stem과 양끝 소자가 모두 같은** 노드는 `dq[0:7] x8` 한 가닥으로 묶음.
  양끝이 다르면 일부러 안 묶습니다(그게 보통 찾던 버그). 끄려면 `--no-bus-groups`.
- 한쪽만 신호에 물린 R/C/L 은 박스 대신 노드에 `·R` / `·C` 로 접어 넣음.
  두 신호 사이를 잇는 R/C 는 박스로 남김.

**CLI에서 미리 거르기**

```bash
primesim-dm graph deck.sp --hide '^VTX' -o g.html      # 이름/subckt 로 숨김
primesim-dm graph deck.sp --only 'XIO_' -o g.html      # 이것만 그림
primesim-dm graph deck.sp --hide-net '^odt' -o g.html  # 노드 숨김
```

**숨겼을 때 노드가 어떻게 되나**

| 노드 상태 | 인스턴스를 숨긴 뒤 |
|---|---|
| 원래 2곳 이상, 보이는 곳 2곳 이상 | 그대로 (일부 숨겨졌으면 점선 + `→`) |
| 원래 2곳 이상, 보이는 곳 1곳 이하 | 같이 사라짐 (꼬리) |
| 원래 1곳(빨강), 그 인스턴스는 보임 | 남음 |
| 원래 1곳(빨강), 그 인스턴스도 숨김 | 사라짐 — 헤더에 개수 경고 |

`stubs` 버튼은 같이 사라진 꼬리 노드를 점선으로 다시 보여줍니다.

**컬럼 배치 저장 (layout)**

```bash
primesim-dm graph deck.sp --save-layout deck.layout.json -o deck.html   # 1. 시작점 저장
# 2. deck.html → edit layout → + column / rename / 박스 드래그 → save layout.json
primesim-dm graph deck.sp --layout deck.layout.json -o deck.html        # 3. 그 배치로 다시 그림
```

- `load layout…` 버튼은 지금 열린 페이지에 바로 적용합니다(몇 개가 맞았는지 숫자로 보고).
- 덱에 새로 생긴 인스턴스는 맨 오른쪽 `unplaced` 컬럼에 모입니다.
- layout 파일은 사람이 읽을 수 있는 JSON이라 버전 관리에 넣어도 됩니다.

**큰 덱**

- `--max-elements N` 을 주면 연결 많은 순으로 N개만 그립니다(기본은 무제한).
  V/I 소스는 예산의 절반까지 우선 보호. 잘린 건 `hidden…` 패널의 `past --max-elements` 에 표시.
- 수백 개 이상이면 `--format dot -o top.dot` 후 `dot -Tpdf top.dot -o top.pdf`.

## E. terminate 옵션 전체

| 옵션 | 기본 | 설명 |
|---|---|---|
| `--type rload\|cload\|rc` | `rload` | 저항 / 커패시터 / 둘 다 |
| `--value` | `1T` | 저항값 |
| `--value2` | `1f` | 커패시턴스 |
| `--to` | `0` | 종단이 돌아갈 노드 |
| `--prefix` | `Rterm_` | 소자 이름 접두사 (덱에 있는 이름과 안 겹치게 번호를 건너뜀) |
| `--exclude REGEX` | | 이 노드는 종단 안 함 |
| `--keep-net REGEX` | | lint와 같은 의미 (애초에 후보에서 뺌) |
| `--nodes-only` | | 노드 목록만 |
| `--sort net\|instance` | `net` | `net`: 다음 결과와 diff 하기 좋음 / `instance`: 블록별로 묶어 검토 |
| `--force` | | 못 읽은 include가 있어도 생성 (기본은 거부) |

## F. 기준 패턴 / 옵션 파일 확인

`primesim_dm/references.py` 의 두 리스트에 기준 파일의 **절대 경로**를 적으면,
`lint` 할 때마다 덱이 그 파일과 **내용이 같은** 파일을 읽고 있는지 확인합니다
(경로·파일명이 달라도 내용이 같으면 통과, 줄바꿈 CRLF/LF 차이는 무시).
리스트가 비어 있으면 아무 검사도 하지 않습니다.

```python
REFERENCE_PATTERN_FILES = [r"/proj/dm/ref/pattern_lpddr5_wr.pat"]
REFERENCE_OPTION_FILES  = [r"/proj/dm/ref/primesim_options.inc"]
```

## G. 한계

- SPICE 문법 전체를 해석하지 않습니다. 결선에 필요한 부분(`.subckt`, `.include`, `.lib`,
  `.global`, `.connect`, `.model ... S`, 주석, 줄 이음)만 봅니다.
- `.param` 식의 문법(괄호 짝 등)은 검사하지 않습니다.
- 시뮬레이터를 실행하지 않습니다.

더 긴 설명과 설계 이유는 [`README.md`](README.md) 에 있습니다.
