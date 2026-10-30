# primesim-dm 사용 가이드

PrimeSim 덱(`.sp`)을 읽어서 결선을 확인하는 도구입니다. 덱은 수정하지 않습니다.

| 명령 | 하는 일 |
|---|---|
| `lint --si` | 노드마다 붙은 소자 수를 세서 2개가 아닌 노드를 찾음 |
| `graph` | 연결을 HTML 그림으로 보여줌 |
| `terminate` | 한쪽만 연결된 노드에 붙일 종단 저항 파일을 만듦 |

준비물은 Python 3.6 이상뿐입니다. 덱은 절대 경로로 지정하세요.

---

## 1. lint --si

```bash
primesim-dm lint /proj/sim/lpddr_write.sp --si -o report.txt
```

| 노드에 붙은 소자 | 뜻 |
|---|---|
| 1개 | 연결 누락, 이름 오타, 또는 안 쓰는 핀 |
| 2개 | 정상 |
| 3개 이상 | 의도하지 않은 분기 (stub) |

```
ERROR si-node-fanout  net txp joins 3 element(s) (XTX, XCH, XSTUB) ...  [lpddr_write.sp:11]
ERROR si-node-fanout  net en_rx joins 1 element(s) (XRX) ...            [lpddr_write.sp:14]
WARN  floating-net    net en_rx is touched only by XRX (port 1)         [lpddr_write.sp:14]

3 error(s), 1 warning(s), 0 note(s)
```

- 화면에는 종류별로 10줄까지만 나오고, `-o` 파일에는 전부 들어갑니다.
- ERROR가 있으면 종료 코드가 1입니다.

## 2. graph

```bash
primesim-dm graph /proj/sim/lpddr_write.sp -o graph.html
```

만들어진 `graph.html`을 브라우저(Chrome / Edge / Firefox)로 엽니다.

| 보이는 것 | 뜻 |
|---|---|
| 회색 박스 | 소자 |
| 흰 알약 | 노드 |
| **빨간 알약** | 한쪽만 연결된 노드 |
| 노란 알약 `·R` `·C` | R/C로 종단된 노드 (정상) |

| 조작 | 하는 일 |
|---|---|
| 휠 / 드래그 | 확대·축소 / 이동 |
| `fit` | 전체 보기 |
| 노드·박스 클릭 | 연결된 것만 강조 (`Esc`로 해제) |
| 검색창 | 이름으로 찾기. `/^XIO_DQ[0-3]$/`처럼 감싸면 정규식 |

**선택 기능**은 `edit layout`을 누르면 나옵니다.

| 버튼 | 하는 일 |
|---|---|
| `select found` | 검색창에 걸린 인스턴스를 전부 선택 |
| `siblings` (또는 더블클릭) | `XIO_DQ0`을 고르면 `XIO_DQ0`~`7`까지 함께 선택 |
| `leaf nets` | 선택한 인스턴스에만 붙은 잔가지 노드 숨기기·보이기 (빨간 노드는 숨겨지지 않음) |
| `hide` / `show all` | 그림에서만 숨기기 / 되돌리기 |

## 3. terminate

```bash
primesim-dm terminate /proj/sim/lpddr_write.sp -o term.inc
```

```spice
Rterm_1   en_rx      0   1T   $ XRX port 1 (lpddr_write.sp:14)
```

한쪽만 연결된 노드마다 `1T` 저항을 접지에 붙이는 줄을 만듭니다. 줄 끝에 어느 인스턴스의 몇 번 포트인지 적혀 있습니다.
**내용을 확인한 뒤** 덱에 `.include 'term.inc'`를 넣으세요.

---

# 부록

## A. 자주 생기는 상황

| 상황 | 해결 |
|---|---|
| `vdd` 같은 전원 노드가 `si-node-fanout`으로 잡힘 | `--keep-net '^vdd' --keep-net '^vss'`로 제외. `0`, `gnd`, `.global` 노드는 자동으로 빠짐 |
| `missing-include` | `--search-dir /proj/models`처럼 include 파일을 찾을 폴더를 추가 |
| `checks-skipped` | 못 읽은 include가 있어서 연결 검사를 멈춘 것. include부터 해결 |
| 읽는 파일이 수천 개라 느림 | `--opaque '/io_model/' --skip '/pdk/'` (부록 D) |
| `off-profile-element` | DM 덱에 잘 안 쓰는 소자(M, D 등)가 있음. 읽게 하려면 `--profile full` |
| 그림이 너무 복잡함 | `--hide '^V'` (빼고 그리기), `--only 'XIO_'` (이것만 그리기) |

## B. 옵션

**lint**

| 옵션 | 설명 |
|---|---|
| `--keep-net REGEX` | 검사에서 뺄 노드 (여러 번 줄 수 있음) |
| `--si-expect N` | 기대 소자 수 (기본 2) |
| `--si-ignore-passives` | R/C/L은 세지 않음 |
| `--summary` / `--all` | 화면에 개수만 / 전부 출력 |
| `--strict` | WARN이 있어도 종료 코드 1 |
| `-v` | INFO까지 출력 |

**graph**

| 옵션 | 설명 |
|---|---|
| `--hide` / `--only REGEX` | 인스턴스 숨기기 / 이것만 그리기 |
| `--hide-net REGEX` | 노드 숨기기 |
| `--rail REGEX` | 선 대신 글자로 표시할 전원 추가 |
| `--save-layout F` / `--layout F` | 컬럼 배치 저장 / 불러오기 (부록 E) |
| `--max-elements N` | 연결이 많은 순으로 N개만 그림 |
| `-o x.svg` / `-o x.dot` | 정적 그림 / graphviz용 |

**terminate**

| 옵션 | 기본 | 설명 |
|---|---|---|
| `--nodes-only` | | 노드 목록만 출력 |
| `--type rload\|cload\|rc` | `rload` | 저항 / 커패시터 / 둘 다 |
| `--value` / `--value2` | `1T` / `1f` | 저항값 / 커패시턴스 |
| `--to` | `0` | 종단이 연결될 노드 |
| `--exclude REGEX` | | 종단하지 않을 노드 |
| `--sort net\|instance` | `net` | 정렬 기준 |
| `--force` | | 못 읽은 include가 있어도 생성 |

## C. 노드를 세는 규칙

- 서로 다른 소자 수로 셉니다. 한 인스턴스가 같은 노드를 두 포트에 연결하면 1개로 셉니다.
- 노드 이름은 대소문자를 구분하지 않습니다.
- 최상위만 봅니다. `.subckt` 안쪽은 읽지 않고 포트 목록만 씁니다.
- 노드 위치는 소자마다 다릅니다.
  - `X`: subckt 이름 앞까지
  - `S`, `B`: 첫 `k=v` 앞까지
  - `W`: `2*(N+1)`개
  - `R` `C` `L` `V` `I`: 2개
- 줄 이음: `+`로 시작하는 줄, 그리고 `'...'` 따옴표가 닫히지 않은 채 끝난 줄은 다음 줄과 이어서 읽습니다.
- `.lib 'file' tt`는 `tt` 섹션만 읽습니다. `.if` 분기는 판단하지 않고 전부 읽습니다.
- 노드를 확정하지 못한 줄은 `unparsed`로 보고합니다.
- 못 읽은 include가 있으면 가짜 경고가 쏟아지지 않도록 연결 검사를 건너뜁니다. 그래도 돌리려면 `--force-connectivity`를 주세요.

## D. 읽는 범위 줄이기

| 옵션 | 동작 |
|---|---|
| `--opaque REGEX` | `.subckt` 포트만 읽고 안쪽은 읽지 않음 (IO 모델, SPF에 사용) |
| `--skip REGEX` | 파일을 아예 열지 않음 (PDK에 사용) |
| `--max-depth N` | include를 N단계까지만 따라감 |

- 패턴은 전체 경로에 거는 정규식입니다. `/pdk/`처럼 슬래시를 넣으세요.
- `.spf`, `.dspf`, `.spef`, `.rcx`는 기본으로 opaque입니다.
- include 상대 경로는 이 순서로 찾습니다: include를 적은 파일의 폴더, 최상위 덱 폴더, 현재 폴더, `--search-dir`.

## E. graph 그리는 규칙과 배치 저장

- 전원(`vdd`, `vss`, `gnd` 등)은 선 대신 박스 밑에 글자로 표시합니다.
- `dq0`~`dq7`처럼 양쪽 끝이 같은 소자에 연결된 노드는 `dq[0:7] x8` 하나로 묶습니다. 양쪽 끝이 다르면 묶지 않습니다.
- 한쪽만 신호에 연결된 R/C/L은 박스 대신 노드에 `·R` / `·C`로 표시합니다.
- 숨기기는 그림에만 적용됩니다. 빨간색 여부는 항상 덱 전체를 기준으로 판정합니다.

```bash
primesim-dm graph /proj/sim/lpddr_write.sp --save-layout layout.json -o graph.html
# graph.html → edit layout → 컬럼 추가, 박스 드래그 → save layout.json
primesim-dm graph /proj/sim/lpddr_write.sp --layout layout.json -o graph.html
```

## F. 기준 패턴·옵션 파일 확인

`primesim_dm/references.py`에 기준 파일의 절대 경로를 적어 두면, `lint`를 돌릴 때마다 덱이 그 파일과 내용이 같은 파일을 읽는지 확인합니다.

자세한 설명은 [`README.md`](README.md)에 있습니다.
