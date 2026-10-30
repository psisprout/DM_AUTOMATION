# primesim-dm-check

이미 있는 PrimeSim 덱을 **읽고 검증하는** CLI. 명령은 세 개뿐입니다.

> 처음 쓰는 거라면 [`GUIDE.md`](GUIDE.md) 부터 보세요 (간단 사용법 + 부록).

| 명령 | 하는 일 | 출력 |
|---|---|---|
| `lint` | 연결성·구조 검사 | `-o report.txt` |
| `graph` | 같은 연결성을 그림으로 | `-o graph.html` |
| `terminate` | 뜬 노드에 터미네이션 생성 | `-o term.inc` |

덱을 **만드는** 기능(`scan` / `init` / `gen`, config, 네이밍 룰)은 이 브랜치에 없습니다.
덱 확인만 할 거면 이쪽이 가볍습니다. 생성 기능은 `claude/dm-element-profile` 쪽에 있습니다.

- **의존성 없음.** Python 3 표준 라이브러리만 (3.6+). pip 설치 불필요.
- **파일 복사만으로 반입 가능.** `primesim-dm/` 디렉토리 통째로 복사하면 끝.
- 읽는 소자는 DM 링크 덱에 실제로 쓰는 것으로 한정됩니다 → [`--profile`](#--profile--읽는-소자-종류를-dm-덱에-맞게-좁힘)
- 상한은 **기본으로 전부 없습니다.** `--max-depth`(include 깊이)와 `--max-elements`
  (그림에 그릴 소자 수)는 주지 않으면 무제한입니다.

---

## 1. 반입 & 동작 확인

사내 환경에 `primesim-dm/` 디렉토리를 복사한 뒤:

```bash
cd primesim-dm
python3 -m unittest discover -s tests     # 220개 테스트, 전부 통과해야 정상
python3 -m primesim_dm lint --help
```

실제 덱으로 바로 확인:

```bash
# 어느 디렉토리에서든 (런처 사용)
/path/to/primesim-dm/primesim-dm lint /proj/sim/lpddr_write.sp

# 패키지 폴더 안에서라면
python3 -m primesim_dm lint /proj/sim/lpddr_write.sp
```

### 윈도우에서

먼저 파이썬이 있는지 확인합니다. `Win+R` → `cmd` 로 명령 프롬프트를 열고:

```cmd
py --version
```

`Python 3.x.x` 가 나오면 준비 끝입니다. `py` 가 없다고 하면 `python --version` 도 해보세요.
둘 다 안 되면 python.org 에서 설치하되 **설치 첫 화면의 "Add python.exe to PATH" 를 체크**하세요.
(`python` 이라고 쳤을 때 Microsoft Store 가 열리면 아직 설치가 안 된 상태입니다.)

그 다음:

```cmd
cd C:\tools\primesim-dm
py -m unittest discover -s tests
py -m primesim_dm lint C:\proj\sim\lpddr_write.sp
```

다른 디렉토리에서 쓰려면 배치 래퍼를 쓰세요:

```cmd
C:\tools\primesim-dm\primesim-dm.bat lint C:\proj\sim\lpddr_write.sp
```

- 읽기는 항상 UTF-8로 하고 깨지는 바이트는 대체 문자로 넘깁니다. 한국어 윈도우 기본
  인코딩(cp949)으로 저장된 덱·모델이 있어도 죽지 않고, 리눅스와 같은 결과가 나옵니다.
- 쓰기는 항상 LF입니다. 윈도우에서 만든 `term.inc` 를 리눅스 팜에서 그대로 돌릴 수 있습니다.
- CRLF로 저장된 덱을 읽는 것도 됩니다.

## 2. `lint` — 기존 덱 검사 (설정 없이)

설정 파일 같은 게 필요 없습니다. **덱 파일만 있으면 됩니다** —
손으로 만든 덱, 남이 준 덱, 예전 프로젝트 덱에 그대로 돌아갑니다.

```bash
# 어느 디렉토리에서든 (런처 사용)
/proj/tools/primesim-dm/primesim-dm lint /proj/sim/lpddr_write.sp

# 패키지 폴더 안에서라면
python3 -m primesim_dm lint /proj/sim/lpddr_write.sp
```

결과는 화면에 출력됩니다. 파일로 남기려면 `-o` 를 쓰세요 — **화면이 `--summary` 나
`--limit` 으로 잘렸어도 파일에는 전체가 들어갑니다** (info 노트 포함). 다음 번 결과와
diff 떠서 뭐가 늘고 줄었는지 보는 용도입니다.

```bash
primesim-dm lint deck.sp --summary -o reports/lint_$(date +%Y%m%d).txt
```

`.include` 상대경로는 이 순서로 찾습니다:

1. **그 `.include` 를 적은 파일**의 디렉토리
2. **최상위 덱**의 디렉토리 ← 라이브러리 안의 `./DB/x.sp` 가 보통 여기 기준입니다
3. 현재 작업 디렉토리
4. `--search-dir` 로 준 경로들 (여러 번 가능)

환경변수(`$MODELS/...`)도 전개됩니다. 그래도 못 찾으면 시도한 디렉토리를 에러에 찍어줍니다.

### 어디까지 읽을지 제한하기 — SPF / PDK

IO 모델을 물면 그 밑에 SPF(기생 추출)와 HSPICE PDK가 줄줄이 딸려옵니다. 파일 수천 개에
트랜지스터 수십만 개인데, **DM 결선 검사에 필요한 건 IO 모델의 `.subckt` 포트뿐**입니다.
안쪽은 볼 이유가 없습니다.

경계를 긋는 방법이 세 가지 있습니다.

| 옵션 | 동작 | 쓸 곳 |
|---|---|---|
| `--opaque REGEX` | **`.subckt` 인터페이스만** 읽고 내부와 그 아래 include는 안 봄 | IO 모델, SPF. **기본 선택지** |
| `--skip REGEX` | 아예 안 엶 | PDK 모델 라이브러리처럼 subckt을 안 쓰는 것 |
| `--max-depth N` | include를 N단계까지만 따라감 | 뭐가 물려 있는지 모를 때 일단 막기 |

```bash
# IO 모델 안쪽은 인터페이스만, PDK는 통째로 제외
primesim-dm lint top.sp --opaque '/io_model/' --skip '/pdk/'

# 일단 2단계까지만
primesim-dm lint top.sp --max-depth 2
```

**`--opaque` 를 먼저 쓰세요.** 포트 정보는 남으니 `port-count` / `undefined-subckt` 검사가
그대로 살아 있고, 잃는 건 볼 필요 없던 내부뿐입니다. `--skip` 은 그 파일이 정의한 subckt이
`undefined-subckt` 로 잡히므로 정말 안 쓰는 파일에만 쓰세요.

`.spf` / `.dspf` / `.spef` / `.rcx` 는 **기본으로 opaque** 입니다 (`--no-default-opaque` 로 해제).

```
files    : 2          ← 8개에서 줄어듦
subckts  : 1 definition(s) available
opaque   : 1 file(s) read for interfaces only (insides not checked)
```

무엇을 안 봤는지 헤더에 항상 찍힙니다. 검사 범위를 모르면 "문제 없음"에 의미가 없어서입니다.

> 패턴은 **전체 경로**에 걸리는 정규식입니다. `pdk` 처럼 짧게 쓰면 상위 디렉토리 이름에도
> 걸리니 `/pdk/` 나 `\.spf$` 처럼 앵커를 넣으세요.

### `--profile` — 읽는 소자 종류를 DM 덱에 맞게 좁힘

링크 덱의 **최상위**에 실제로 올라오는 것은 일곱 가지밖에 없습니다. IBIS 버퍼,
채널 S-parameter, 채널 broadband SPICE 모델, package / SPF IO 모델 같은 `.subckt` 호출,
W element, 그리고 R/L/C 와 소스입니다. 그게 `dm` 프로필이고 **기본값**입니다.

| 소자 | DM 덱에서의 정체 | 노드 경계 |
|---|---|---|
| `X` | `.subckt` 호출 — broadband 모델, SPF IO 모델, package | `=` 없는 토큰 중 **마지막 하나(subckt 이름)를 제외하고** 앞까지 |
| `S` | S-parameter 채널 (Touchstone) | 첫 `k=v` 전까지 |
| `B` | IBIS 버퍼 | 첫 `k=v` 전까지 |
| `W` | coupled transmission line | `N=` 을 읽어서 `2*(N+1)` 개 |
| `R` `C` `L` | 수동 소자 | 2개 |
| `V` `I` | 소스 | 2개 |
| `K` | L 두 개 사이의 상호 인덕턴스 | 노드 없음 |

이 밖의 소자(트랜지스터, 다이오드, 제어 소스 …)가 최상위에 나오면 **읽지 않고
보고만 합니다.**

```
off prof : 2 line(s) of other element types, not read

WARN  off-profile-element 2 line(s) are element types the 'dm' profile does not
      read, so their nodes are missing from the connectivity below:
      D (diode), M (MOSFET). Pass --profile full to read them.
INFO  off-profile-element M_esd pad_tx 0 0 0 nch w=10u   [top.sp:35]
INFO  off-profile-element D_esd pad_tx vddq dio          [top.sp:36]
```

어떤 줄이었는지 줄번호와 함께 다 찍힙니다. 조용히 빼는 게 아니라 **영향 있었는지 한 번은
눈으로 확인하라는 리스트**입니다. 읽히게 하려면 `--profile full` 을 주시면 전에 하던
대로 전부 읽습니다.

```bash
primesim-dm lint  top.sp               # dm (기본)
primesim-dm lint  top.sp --profile full
primesim-dm graph top.sp --profile full -o g.html
```

`lint` / `terminate` / `graph` 에 같이 붙어 있어서 세 명령이 **동일한 연결성을** 봅니다.

> `Y` 처럼 어떤 프로필에도 없는 글자는 `off-profile` 이 아니라 기존대로
> `unparsed` 입니다. "이 덱은 안 쓰는 소자"와 "노드 경계를 모르겠다"는 다른 문제라
> 섞지 않습니다.

#### `.subckt` 속은 여전히 안 읽습니다

프로필은 **최상위에만** 적용됩니다. broadband 모델 속은 `E`/`G` 제어 소스로 가득하지만,
`.subckt` 바디는 어차피 읽지 않으니 `off-profile` 로 뜨지 않습니다. 포트 목록(인터페이스)만
가지고 있습니다.

```
subckts  : 2 definition(s) available (insides not read)
```

### IBIS / S-parameter 파일 자체 검사

`.include` 는 없으면 에러가 나지만, **IBIS 와 Touchstone 파일은 소자 줄에서 이름으로
불립니다.** 아니면 아무도 안 봅니다 — S-parameter가 안 읽힌 채널은 에러가 아니라
**개방(open)으로 돌아갑니다.**

| 코드 | 언제 |
|---|---|
| `missing-model-file` (ERROR) | `B` 의 `file=` / `S` 의 `tstonefile=` 가 디스크에 없을 때 |
| `sparam-ports` (ERROR) | `S` 의 노드 수가 `.sNp` 의 포트 수와 안 맞을 때 |
| `ibis-incomplete` (WARN) | `B` 에 `file=` / `model=` / `buffer=` 가 빠졌을 때 |

```
ERROR missing-model-file Bdrv names IBIS file dq_v6.ibs, which is not on any of
      the directories searched. Add --search-dir, or fix the path.  [top.sp:8]
ERROR sparam-ports       S_board has 2 node(s) but board_dq.s4p is a 4-port
      file; it takes 4 node(s), or 5 with a reference node  [chan.model:1]
```

포트 수는 **확장자 `.s4p` 에서** 읽습니다. 노드는 N개 또는 reference 노드를 붙인
N+1개만 정상으로 봅니다. 확장자가 `.sNp` 꼴이 아니면 **아무 주장도 하지 않습니다.**

파일 이름이 `.model` 에 있는 일반적인 형태도 따라갑니다:

```
S_board ball_tx ball_rx ball_tx_ret ball_rx_ret mname=ch_dq
.model ch_dq S tstonefile='board_dq.s4p'
```

파일을 찾는 순서는 `.include` 와 동일합니다 (이름 부른 파일의 디렉토리 → 최상위 덱
디렉토리 → cwd → `--search-dir`).

### `--si` — signal 노드는 소자 2개 (SI 덱 전용)

SI 덱에서 signal 노드는 **드라이버 하나 + 리시버 하나**로 딱 2개가 붙는 게 정상입니다.
1개면 어딘가 안 이어진 것이고, 3개 이상이면 의도치 않은 분기(stub)입니다.

```bash
primesim-dm lint deck.sp --si -o report.txt
```

```
ERROR si-node-fanout  net txp1 joins 3 element(s) (XDRV1, XCH1, XSTUB);
                      an SI node should join exactly 2   [deck.sp:15]
ERROR si-node-fanout  net rxp2 joins 1 element(s) (XCH2);
                      an SI node should join exactly 2   [deck.sp:22]
```

ERROR 라 **exit 1** 입니다 — 회귀 스크립트에 그대로 물립니다.

**기본은 꺼져 있습니다.** 이건 SPICE 규칙이 아니라 *특정 종류의 덱*에 대한 규칙이라서요.
전원 노드나 제어 노드는 1개나 열 개가 붙는 게 정상이고, 그런 덱에 켜면 정작 봐야 할
것이 묻힙니다.

| 옵션 | 뜻 |
|---|---|
| `--si` | 규칙 켜기 |
| `--si-expect N` | 기대 개수 (기본 2) |
| `--si-ignore-passives` | R/C/L 은 안 셈 — 드라이버+리시버+**종단**을 2개로 볼 때 |

- **레일/`.global`/`--keep-net` 은 검사에서 빠집니다.** floating 검사가 빼는 것과 같은
  이유로, 그건 signal path 가 아닙니다.
- 소자 개수는 **서로 다른 소자** 기준입니다. 한 인스턴스가 같은 net 을 두 포트에 물려도
  1개로 셉니다.
- 덱 경계(자극원이 붙는 입력, 프로브만 있는 출력)는 1개로 잡힐 수 있습니다.
  그건 `--keep-net` 으로 빼세요.

### 기준 패턴 / 옵션 파일 확인

"이 덱이 **약속된 패턴과 옵션**으로 돌고 있나"를 매 `lint` 마다 확인합니다.
설정하는 곳은 딱 한 파일입니다:

**`primesim_dm/references.py`** — 맨 아래 두 리스트에 **절대 경로**를 적으세요.

```python
REFERENCE_PATTERN_FILES = [
    r"/proj/dm/ref/pattern_lpddr5_wr.pat",
    r"/proj/dm/ref/pattern_lpddr5_rd.pat",     # 여러 개 가능
]

REFERENCE_OPTION_FILES = [
    r"/proj/dm/ref/primesim_options.inc",
]
```

**경로가 아니라 내용으로 맞춰봅니다.** 다른 디렉토리에 다른 이름으로 복사돼 있어도
내용이 같으면 통과입니다 — 중요한 건 파일이 어디 있느냐가 아니라 덱이 그 패턴/옵션으로
돌고 있느냐라서요.

```
WARN  improper-option  no file this deck reads has the same content as the
                            reference option file /proj/dm/ref/primesim_options.inc
INFO  reference-file        pattern file pattern_lpddr5_wr.pat is in this deck as
                            /proj/sim/DB/copied_pattern.pat
```

- **줄바꿈(CRLF/LF)과 줄 끝 공백은 무시**합니다. 같은 파일을 윈도우에서 받아온 사본이
  다른 파일로 취급되면, 정작 통과시켜야 할 셋업에서 경고가 뜹니다.
- **`--skip` 한 파일도 후보에 넣습니다.** 기준 파일은 보통 `--skip` 을 겨누는 그런
  디렉토리에 있고, "네가 건너뛰었잖아"는 없다고 보고할 이유가 못 됩니다.
- **기준 파일 자체를 못 읽으면 그것도 경고합니다.** 기준이 사라져서 조용히 통과하는 게
  제일 나쁜 결과라서요.
- **두 리스트가 비어 있으면 아무 검사도 안 합니다.** 빈 문자열 항목도 무시합니다.

### `.lib` 는 지정한 섹션만 읽습니다

`.lib 'corners.lib' tt` 는 `tt` 섹션만 활성화합니다. 파일 전체를 읽으면 코너별 섹션이
전부 들어와서 같은 subckt·같은 소자 이름이 코너 수만큼 중복됩니다.

```
files    : 2
           /proj/sim/top.sp
           /proj/DB/corners.lib [.lib tt]      ← 어느 섹션을 읽었는지 표시
subckts  : 1 definition(s) available            ← 3개 코너였어도 1개
```

- `.lib <name>` ~ `.endl` 밖에 있는 내용은 섹션과 무관하게 항상 읽습니다.
- 같은 파일을 다른 섹션으로 두 번 불러도 각각 읽습니다.
- `.include` 로 부르면 어떤 섹션도 활성화되지 않습니다 (섹션 정의만 있는 상태).
- 비활성 구간은 빈 줄로 치환해서 **줄 번호가 그대로 유지**됩니다.

### 못 읽은 파일이 있으면 연결성 검사를 건너뜁니다

`.include` 하나를 못 읽으면 그 안의 소자가 통째로 빠지고, 그 소자에 붙어 있던 net들이
전부 "한쪽만 연결됨"으로 보입니다. 파일 하나 때문에 `floating-net` 수백 개가 쏟아지면
정작 원인인 그 한 줄이 묻힙니다.

그래서 **읽지 못한 include가 하나라도 있으면 `floating-net` / `isolated-instance` 검사를
아예 돌리지 않고** 이렇게 알려줍니다:

```
WARN  checks-skipped   connectivity checks skipped: the netlist is incomplete,
                       and roughly 98 net(s) look one-sided purely because of
                       that. Fix the includes first, or pass --force-connectivity.
```

include를 먼저 해결하고 다시 돌리는 게 맞습니다. 굳이 보고 싶으면 `--force-connectivity`.

```
primesim-dm deck check
==================================================================
files    : 1
elements : 13 at the top level
nets     : 14
unparsed : 1 line(s)  <- checks below do not cover these

ERROR duplicate-name     element XIO1 is defined twice (first at deck.sp:13)
ERROR missing-include    cannot find included file: models.inc (from deck.sp:2)
ERROR port-count         XIO2 passes 4 node(s) to subckt io_cell, which declares 5 port(s)
ERROR undefined-subckt   XRX1 calls subckt 'mem_io_cell', which is not defined in any file read
WARN  floating-net       net en_rx is touched only by XRX1 (port 4)
INFO  merged-net         ball_dq0 and ball_dq1 are one node (Rshort = 0 ohm)

4 error(s), 4 warning(s), 2 note(s)
```

검사 항목:

| 코드 | 등급 | 내용 |
|---|---|---|
| `missing-include` | ERROR | `.include`/`.lib` 경로에 파일이 없음 |
| `undefined-subckt` | ERROR | X가 부르는 subckt이 어디에도 정의 안 됨 |
| `port-count` | ERROR | X의 노드 개수 ≠ `.subckt` 포트 개수 |
| `duplicate-name` | ERROR | 같은 이름 소자가 두 번 |
| `isolated-instance` | ERROR | 인스턴스의 모든 노드가 다른 데 안 붙음 |
| `floating-net` | WARN | 한 곳만 붙은 net (`--keep-net` 로 제외 가능) |
| `unparsed-line` | WARN | 노드를 확정 못 한 줄 — **검사 범위 밖임을 명시** |
| `merged-net` | INFO | 0옴 저항이나 `.connect` 로 두 net이 사실상 한 노드 |
| `improper-pattern` | WARN | 기준 패턴 파일과 내용이 같은 파일이 덱에 없음 |
| `improper-option` | WARN | 기준 옵션 파일과 내용이 같은 파일이 덱에 없음 |
| `si-node-fanout` | ERROR | **`--si` 일 때만.** signal 노드에 붙은 소자가 2개가 아님 |

에러가 있으면 exit 1, `--strict` 면 경고에도 exit 1입니다.

**파싱 못 한 줄은 추측하지 않고 신고합니다.** 소자 종류마다 노드 개수 규칙이 달라서
(`M`은 4개, `Q`는 3~4개, `W`는 `N=`에 따라, `S`/`B`는 파라미터 직전까지) 전부 맞히는 파서는
없습니다. 확신이 안 서는 줄은 `unparsed`로 빼고 개수를 헤더에 찍습니다 —
"이 검사 결과가 덱의 몇 %를 봤는지" 모르면 통과했다는 말에 의미가 없어서입니다.

net 이름은 SPICE 규칙대로 대소문자를 구분하지 않습니다 (`PAD_DQ0` = `pad_dq0`).

**한 인스턴스가 같은 net 을 두 포트에 물린 건 "연결"로 세지 않습니다.**

```
X_RDL bump_vssi_3 bump_vssi_3 pad_a lone rdl
```

SPICE 문법으로는 두 핀이 한 노드인 게 맞습니다. 하지만 DM 검사가 묻는 건 "이 노드가
**다른 무언가**에 이어져 있나"이고, 인스턴스가 자기 노드를 두 번 짚은 건 그 답이
아닙니다. 그래서 붙은 **소자 개수**로 세고, 위 경우는 이렇게 잡힙니다:

```
WARN  floating-net  net bump_vssi_3 is touched only by X_RDL and nothing else, at ports 0, 1
```

포트 번호를 같이 찍는 건, 파일만 보면 연결된 것처럼 보이는 게 바로 이 경우라서입니다.
`graph` 도 같은 기준이라 빨갛게 나오고, `terminate` 도 후보로 올립니다.

## 3. `terminate` — 뜬 노드에 터미네이션 달기

한쪽만 연결된 노드를 찾아 R/C 터미네이션 줄을 만들어 줍니다. `lint` 와 같은
옵션(`--search-dir` / `--skip` / `--opaque` / `--max-depth` / `--keep-net`)을 그대로 씁니다.

```bash
primesim-dm terminate deck.sp -o term.inc
```

```spice
*==========================================================================
* termination for 8 node(s) nothing else connects to
* source: /proj/sim/deck.sp
*
* REVIEW BEFORE USING. A one-sided net is just as often a
* connection someone forgot as a pin nobody uses; the port
* each line came from is noted so you can tell them apart.
*==========================================================================
Rterm_1   en_rx      0   1T   $ XRX port 4 (deck.sp:5)
Rterm_2   odt_en     0   1T   $ XIO port 5 (deck.sp:4)
```

**덱을 고치지 않고 별도 `.inc` 로 뽑습니다.** 한쪽만 붙은 net은 안 쓰는 핀일 수도,
누가 빠뜨린 결선일 수도 있는데 도구는 그 둘을 구분하지 못합니다. 그래서 줄마다
**어느 인스턴스의 몇 번 포트에서 나왔는지** 적어두니, 보고 판단한 뒤 덱에
`.include 'term.inc'` 를 넣으시면 됩니다.

| 옵션 | 기본 | 설명 |
|---|---|---|
| `--type rload\|cload\|rc` | `rload` | 저항 / 커패시터 / 둘 다 |
| `--value` | `1T` | 저항값 |
| `--value2` | `1f` | 커패시턴스 |
| `--to` | `0` | 터미네이션이 돌아갈 net |
| `--prefix` | `Rterm_` | 소자 이름 접두사 |
| `--exclude REGEX` | | 이 net은 건드리지 않음 (반복 가능) |
| `--nodes-only` | | **소자 안 만들고 노드 목록만** 출력 |
| `--sort net\|instance` | `net` | 정렬 기준 (아래 참고) |

```bash
# 커패시터로, vss 기준, 테스트핀은 제외
primesim-dm terminate deck.sp --type cload --value2 5f --to vss --exclude '^tm_'

# 일단 목록만 보고 직접 판단
primesim-dm terminate deck.sp --nodes-only
```

생성되는 소자 이름은 덱에 이미 있는 이름과 겹치지 않게 번호를 건너뜁니다.

### `--sort instance` — 인스턴스별로 묶어서

```bash
primesim-dm terminate deck.sp --sort instance -o term.inc
```

```
*--- XIO_DQ0  (3 node(s)) -------------------------------------------------
Rterm_1  txd0      0  1T   $ XIO_DQ0 port 3 (deck.sp:3)
Rterm_2  rxd0      0  1T   $ XIO_DQ0 port 4 (deck.sp:3)
Rterm_3  odt0      0  1T   $ XIO_DQ0 port 5 (deck.sp:3)
*
*--- XPKG_DQ0  (1 node(s)) ------------------------------------------------
Rterm_4  ball_dq0  0  1T   $ XPKG_DQ0 port 1 (deck.sp:4)
```

**인스턴스는 덱에 나온 순서, 그 안에서는 포트 순서**입니다. 검토를 원래 그렇게 하니까요 —
블록 하나씩, 그 블록의 핀 목록과 대조해서. 인스턴스마다 몇 개인지도 헤더에 찍힙니다.

기본값이 `net` 인 이유는 **저장해둔 파일과 다음 번 결과를 diff** 뜨기 위해서입니다.
덱에 인스턴스 하나를 끼워 넣으면 `instance` 정렬은 그 아래가 전부 밀리지만, `net`
정렬은 새로 생긴 줄만 늘어납니다.

**읽지 못한 include가 있으면 거부합니다.** 그 파일 안의 소자가 빠져서 뜬 것처럼 보이는
net까지 터미네이션하면 진짜 문제를 덮어버리기 때문입니다. `--force` 로 넘길 수는 있습니다.

## 4. `graph` — 연결성을 그림으로

`lint` 가 읽은 것과 똑같은 소자·net을 그대로 그립니다. 리포트 400줄을 읽는 대신
모양으로 보려는 것이고, 설정 파일은 필요 없습니다.

```bash
primesim-dm graph deck.sp --skip '/proj/pdk/' -o deck.html   # 확대/축소 되는 뷰어
primesim-dm graph deck.sp --skip '/proj/pdk/' -o deck.svg    # 보고서에 붙일 그림
```

**출력 형식은 `-o` 확장자로 정합니다** (`.html` / `.svg` / `.dot`). `--format` 으로
직접 줄 수도 있습니다.

| 형식 | 쓸 곳 |
|---|---|
| `html` | **화면에서 볼 때.** 휠 확대/드래그 이동/검색/클릭 추적 |
| `svg` | 보고서·문서에 붙일 때. 정적 그림 |
| `dot`  | graphviz 로 넘길 때 (아래 "덱이 클 때") |

둘 다 파일 하나로 완결돼 있습니다 — **외부 라이브러리도 네트워크도 안 씁니다.**
pip도 인터넷도 없는 환경에서 도는 게 이 도구의 전제라, CDN에서 뭘 받아오는 뷰어는
빈 화면만 보여주기 때문입니다. 밝은/어두운 테마 양쪽에서 읽히게 색이 들어 있습니다.

### 브라우저

Chrome / Edge / Firefox 에서 됩니다. 파일 하나라 그냥 더블클릭해서 열면 됩니다
(`file://` 로 열려도 전부 동작).

포인터 캡처와 Pointer Events를 안 쓰도록 만들어서, **Pointer Events가 기본으로 꺼져
있는 구버전 Firefox ESR(59 미만)에서도 드래그가 됩니다** — 그런 환경에서는 마우스
이벤트로 자동 전환합니다.

`copy` 버튼은 `file://` 에서 클립보드 권한이 없는 브라우저(주로 Firefox)에서는
텍스트 상자를 띄워 대신 복사합니다. `save layout.json` 은 그냥 파일로 내려받습니다.

### `html` 뷰어 조작

소자 80개짜리는 한 화면에 안 들어가고, 들어가도 글씨가 안 보입니다.

| 조작 | 하는 일 |
|---|---|
| 휠 | 커서 위치 기준 확대/축소 |
| 드래그 | 이동 |
| `fit` / `0` | 전체가 보이게 맞춤 (열면 이 상태) |
| `1:1` | 실제 크기 |
| `+` `-` | 확대 / 축소 |
| 검색창 | net·인스턴스 이름으로 필터. **버스로 묶이기 전 원래 net 이름**(`dq3`)으로도 찾힙니다. `/regex/` 로 감싸면 정규식 |
| 노드 클릭 | 그 net에 붙은 소자만 남기고 나머지는 흐리게 — 한 net을 끝까지 따라갈 때 |
| `Esc` | 강조 해제 |

읽는 법:

| 그려지는 것 | 뜻 |
|---|---|
| 회색 박스 | 소자. 이름 + subckt(또는 소자 종류) |
| 박스 밑 `⏚ vddq, vss` | 그 소자가 물린 전원/접지 |
| 흰 알약 | net. 굵은 선이면 버스 |
| **노란 알약 + `·R` / `·C`** | 인스턴스 하나만 붙은 net. **문제가 아니라** R/C 로 종단된 것 |
| **빨간 점선 + 빨간 알약** | 한쪽만 붙은 net — `terminate` 가 집어갈 후보 |

그림이 hairball이 되지 않게 두 가지를 합니다. **둘 다 그림 자체보다 중요합니다.**

**1. 전원은 선으로 안 그립니다.** vdd/vss는 모든 인스턴스에 물려서, 선으로 그리면
신호 경로가 파묻힙니다. 그래서 박스마다 스텁으로 적습니다 — 회로도가 하는 방식입니다.
기본 패턴은 `0`/`gnd`/`vss`/`vdd`/`vcc`/`avdd`/`avss`/`vbb`/`vpp` 이고,
프로젝트 전용 레일은 `--rail '^vddca'` 처럼 추가합니다. `--no-rails` 로 끌 수 있지만
말 그대로 hairball이 됩니다.

**2. 버스는 한 가닥으로 묶습니다.** `dq0`~`dq7` 이 똑같이 결선돼 있으면 엔지니어가
확인할 건 8개가 아니라 1개입니다. 그래서 **stem이 같고 양끝 소자가 같은** net만
`dq[0:7] x8` 로 합칩니다.

> **stem은 같은데 양끝이 다르면 안 합칩니다.** 이게 핵심입니다. `dq[0:1]` 과 `dq[2:3]`
> 이 따로 그려졌다면 DQ 버스 절반이 다른 데로 갔다는 뜻이고, 보통 그게 찾던 버그입니다.
> 굳이 다 펼쳐 보려면 `--no-bus-groups`.

### 그림은 읽은 만큼만 그립니다

헤더에 소자/net 개수와 **무엇이 빠졌는지**를 항상 찍습니다. `--skip` 한 파일의 소자는
안 그려지고, 못 읽은 include가 있으면 `INCOMPLETE` 이 뜹니다. 절반이 빠진 걸 조용히
감춘 그림은 그림이 없는 것보다 나쁩니다.

```
hbm_tx_rx.sp
21 element(s), 19 net node(s)
1 one-sided net node(s), drawn in red
```

### R / C 는 인스턴스가 아닙니다

`C_RCV_BALL_EP` 같은 종단 커패시터는 설계 블록이 아니라 **그 노드의 성질**입니다.
박스로 그리면 인스턴스 수가 두 배가 되고 정작 서로 신호를 주고받는 것들이 밀려납니다.
그래서 **한쪽만 신호에 물린 2단자 수동소자(R/C/L)는 그 노드에 접어 넣습니다.**

```
26 element(s), 40 net node(s)
24 passive(s) folded into their node, not drawn as instances
```

- 접힌 소자는 net 알약에 `·R` / `·C` 로 표시되고 **노란색**이 됩니다.
- **두 신호 사이를 잇는 R/C 는 박스로 남습니다.** 그건 연결 경로라서, 접으면 그림이
  보여주려던 길이 끊깁니다.
- **한쪽만 연결됐는지 판정은 안 바뀝니다.** `lint` 는 net 에 붙은 소자를 다 세므로
  `ball_dq0` (XPKG + C) 은 2개짜리 — 빨강이 아닙니다. 그림도 같은 수를 씁니다.

### "이건 왜 빨간색이 아니지?" — 노드를 선택하면

노드 하나만 선택하면 왼쪽 아래에 작은 창이 뜹니다.

```
[ball_dq0  net]
  instances  XPKG_DQ0
  passives   C_RCV_BALL_EP0 180f
  elements   2
  → not red because 1 passive(s) (C) sit on it as well as the instance,
    so the deck has 2 elements on this net.
```

인스턴스를 고르면 위치·전원·물린 net 목록이 나옵니다. **선택이 바뀔 때 한 번만**
만들기 때문에 노드가 수백 개여도 느려지지 않습니다.

### 검색창 — 평문과 `/regex/`

```
XIO                 평문. 부분 문자열로 찾음 (대소문자 무시)
rxd[0:3]            평문이라 대괄호도 글자 그대로 — 버스 라벨이 그대로 찾힘
/^XIO_DQ[0-3]$/     정규식
/^(odt|rxd)\d$/     정규식
```

**`/.../` 로 감쌀 때만 정규식입니다.** net 라벨이 실제로 `dq[0:7]`, `clk<3>` 처럼
생겼는데 이게 전부 정규식 문법이기도 해서, 무조건 정규식으로 보면 **화면에 보이는 대로
쳤을 때 엉뚱한 게 걸립니다.** 평문을 기본으로 두면 그럴 일이 없습니다.

- 대소문자는 기본으로 무시합니다 (`/.../i` 를 안 붙여도 됨)
- **정규식은 필드별로 맞춰봅니다** — 이름 / subckt / 버스 뒤의 각 net 이름을 따로.
  이것들을 한 줄로 이어 붙여놓고 맞추면 `^...$` 가 절대 안 맞는데, 앵커야말로 정규식을
  쓰는 주된 이유라서요.
- 몇 개 걸렸는지 옆에 숫자로 나옵니다 (`12 found (regex)`)
- **컴파일이 안 되는 패턴은 빨갛게 알려줍니다.** 안 그러면 "0개 매치"와 구분이 안 됩니다
- `select found` 도 같은 규칙을 씁니다

### 잔가지 접기 — `leaf nets` / `stubs`

| 버튼 | 하는 일 |
|---|---|
| `leaf nets` | **선택한 인스턴스에만** 매달린 net 을 껐다 켰다. 비트별 IO 면 8개 골라서 한 번에 |
| `stubs` | 숨긴 인스턴스 때문에 **딸려 나간 net** 을 도로 보이게 (점선). 다시 누르면 숨김 |

**`leaf nets` 는 빨간 net 을 절대 건드리지 않습니다.** 종단된 잔가지는 접어도 되지만
한쪽만 연결된 net 은 찾던 문제라서요. 접힌 것들은 `hidden…` 패널의 `nets` 에 모이니
거기서 골라 되살리면 됩니다.

### 안 볼 인스턴스 빼기

덱 검증 중에 그림이 복잡하면 인스턴스를 빼고 볼 수 있습니다. **덱에서 빼는 게 아니라
그림에서만 빼는 것**이고, 인스턴스 이름이나 그게 부르는 subckt 로 고릅니다.

```bash
primesim-dm graph deck.sp --hide '^VTX'        # 자극원은 빼고 보기
primesim-dm graph deck.sp --hide 'pkg_'        # subckt 이름으로도 됨
primesim-dm graph deck.sp --only 'XIO_'        # IO 만 보기
primesim-dm graph deck.sp --hide-net '^odt'    # net 을 빼고 보기
```

뷰어에서는 `[edit layout]` → **인스턴스든 net(알약)이든 선택** → `hide`.
(비트별 IO면 하나 더블클릭해서 8개 잡고 한 번에.) `save layout.json` 에 같이
저장돼서 다음에 `--layout` 으로 그릴 때도 빠진 채로 나옵니다.

### 인스턴스를 빼면 딸려 나가는 net

`V_INT_DQ0` 과 `X_DQ0` 이 `a_dq0` 으로 이어져 있었다면, `V_INT_DQ0` 을 빼는 순간
`a_dq0` 은 **남은 한쪽에 매달린 꼬리**일 뿐입니다. 연결을 보여주지 못하면서 자리만
차지하니 같이 사라집니다.

**단, 덱에서 원래부터 한쪽만 연결됐던 net(빨강)은 남습니다.** 그건 꼬리가 아니라
찾던 문제라서요. 규칙으로 쓰면:

| net 상태 | 인스턴스를 숨긴 뒤 |
|---|---|
| 원래 2군데 이상 → 보이는 게 2군데 이상 | 그대로 (일부가 숨겨졌으면 점선 + `→`) |
| 원래 2군데 이상 → 보이는 게 1군데 이하 | **같이 사라짐** (꼬리) |
| 원래 1군데 (빨강) → 그 인스턴스가 보임 | **남음** (문제니까) |
| 원래 1군데 (빨강) → 그 인스턴스도 숨김 | 사라짐 — **헤더가 몇 개인지 경고** |

### 숨긴 것 목록에서 골라 되살리기

툴바의 `hidden…` 을 누르면 패널이 열리고 **세 묶음**으로 보여줍니다.

| 묶음 | 내용 |
|---|---|
| `instances` | 내가 숨긴 인스턴스 (클릭하면 그것만 복구) |
| `nets` | 내가 숨긴 net (클릭하면 복구, 원래 빨강이었으면 `was one-sided!` 표시) |
| `dropped with them` | 숨긴 것 때문에 딸려 나간 net — **왜 사라졌는지**까지 |

`instances` / `nets` 는 한 줄씩 클릭해서 원하는 것만 되살립니다. 전부 되돌리려면
`show all`.

> `dropped with them` 은 직접 복구하는 게 아니라, 딸려 나가게 만든 인스턴스를
> 되살리면 같이 돌아옵니다. "그 net 어디 갔지"가 제일 먼저 나오는 질문이라
> 목록으로 보여주는 겁니다.

**여기서 제일 중요한 건 숨겨도 연결성 판정이 안 바뀐다는 겁니다.** `XPKG_DQ0` 을
숨겼다고 `pad_dq0` 이 "한쪽만 연결됨(빨강)"이 되면, 검증하려고 쓰는 도구가 거짓
경보를 만드는 셈입니다. 그래서 **한쪽만 연결됐는지는 언제나 덱 전체로 판정하고**,
숨김은 그리기에만 적용됩니다.

| 그려지는 것 | 뜻 |
|---|---|
| 빨간 알약 | 덱에서 진짜 한쪽만 연결됨 |
| **회색 점선 알약 + `→`** | 연결은 돼 있는데 **상대가 화면 밖**(숨긴 인스턴스) |

**숨기는 건 공짜가 아닙니다.** 붙어 있던 인스턴스를 전부 숨기면 그 net은 아예 안
그려지는데, 그중에 진짜 문제가 있었으면 그림에서 사라집니다. 그래서 헤더에 그 수를
찍습니다:

```
18 element(s), 32 net node(s)
16 one-sided net node(s), drawn in red
8 instance(s) hidden; 8 net(s) reach them and are drawn dashed
8 net(s) not drawn at all: every instance on them is hidden - 8 of those were one-sided
```

마지막 줄이 "지금 8개를 못 보고 있다"는 뜻입니다. 뷰어에서도 같은 내용이 상태줄에 뜹니다.

> `lint` 에는 숨기기를 넣지 않았습니다. 리포트에서 인스턴스를 빼는 건 에러를 빼는
> 것과 같아서요. 읽는 범위를 줄이려면 `--opaque` / `--skip` 을 쓰세요.

### 컬럼을 직접 정해서 배치하기

자동 배치는 **연결 관계만 보고 하는 추측**입니다. 이 넷이 TX 쪽이고 저건 패키지라는 건
도구가 알 수 없습니다. 그래서 뷰어에서 직접 컬럼을 만들고 인스턴스를 끌어다 놓을 수
있고, **그 배치는 파일로 남아서 다음에 다시 그릴 때도 그대로 나옵니다.**

```bash
# 1. 지금 배치를 출발점으로 저장
primesim-dm graph deck.sp --save-layout deck.layout.json -o deck.html

# 2. deck.html 을 열고 [edit layout] → 컬럼 만들고 인스턴스 드래그
#    → [save layout.json] 으로 내려받아 deck.layout.json 을 덮어씀

# 3. 이제부터는 그 배치로 그려집니다
primesim-dm graph deck.sp --layout deck.layout.json -o deck.html
```

`[edit layout]` 을 누르면:

| 조작 | 하는 일 |
|---|---|
| 컬럼 이름 클릭 | 그 컬럼 선택 (`▸` 표시) |
| `+ column` | 선택한 컬럼 **뒤에** 새 컬럼 삽입 |
| `- column` | 선택한 컬럼 삭제. **비어 있어야 지워집니다** |
| `rename` | 컬럼 이름 변경 (`TX`, `package`, `RX` 처럼) |
| 인스턴스 클릭 | 선택 (하나) |
| **더블클릭** | **같은 stem을 쓰는 인스턴스 전부 선택** (`XIO_DQ0` → `XIO_DQ0`~`7`) |
| Ctrl(⌘)+클릭 | 선택에 하나 추가 / 빼기 |
| Shift+드래그 (빈 곳) | 사각형으로 쓸어서 여러 개 선택 |
| `siblings` | 선택한 것들의 형제를 전부 선택 (더블클릭과 같음) |
| `select found` | 검색창에 친 문자열에 맞는 인스턴스를 전부 선택 |
| 인스턴스 드래그 | **선택한 것 전부**를 컬럼 간 이동 + 컬럼 안 순서 지정 |
| `Esc` | 선택 해제 |
| `load layout…` | **layout 파일을 읽어 지금 페이지에 적용** |
| `save layout.json` | 레이아웃 파일 내려받기 |
| `copy` | 같은 내용을 클립보드로 (파일 다운로드가 막힌 환경용) |
| `revert` | 열었을 때 상태로 되돌리기 |

### IO를 비트별로 인스턴스화했을 때

`XIO_DQ0` ~ `XIO_DQ7` 처럼 비트마다 인스턴스를 부르면 8개(또는 64개)를 하나씩 끄는 건
말이 안 됩니다. 그래서 **인스턴스 하나를 더블클릭하면 이름 stem이 같은 나머지가 전부
같이 잡힙니다.** 그 상태로 하나만 끌면 전부 같이 이동하고, 원래 순서(0,1,2…)를 유지한
채로 들어갑니다.

stem 규칙은 net 버스를 묶을 때 쓰는 것과 **같은 규칙**입니다 (`XIO_DQ0` → `XIO_DQ`,
`XIO_DQ<0>` / `XIO_DQ[0]` 도 동일). 둘이 다르면 `dq0` 과 `XIO_DQ0` 이 서로 다른 기준으로
묶여서 헷갈리기만 합니다.

net(알약)은 **끌지 않습니다.** 인스턴스 컬럼이 정해지면 net은 자기 양끝 중 가장 왼쪽
컬럼 바로 뒤 칸으로 자동으로 갑니다. 사람이 정할 값어치가 있는 건 "어느 블록이 어느
단계인가"이지 net 하나의 좌표가 아니라서입니다.

자동 배치도 비트별 덱을 감안합니다: **서로 연결되지 않은 부분은 오른쪽으로 밀지 않고
0번 컬럼부터 다시 시작해 세로로 쌓습니다.** 비트별 IO는 모양이 똑같은 독립된 덩어리가
N개인 셈이라, 오른쪽으로 늘어놓으면 8비트짜리가 컬럼 18개로 퍼지면서 정작 같은 비트끼리
줄이 안 맞습니다. 쌓으면 2~3컬럼으로 정리됩니다.

### 뷰어에서 바로 불러오기 — `load layout…`

CLI 를 거치지 않고 **지금 열려 있는 페이지에** layout 을 적용합니다. 남이 만든 배치를
열어보거나, 두 배치를 번갈아 보거나, 덱은 그대로인데 배치만 바꿔볼 때 씁니다.

```
[layout applied: nand_wr.layout.json]
  columns     5
  instances   112 of 116 placed
  hidden      8 instance(s), 3 net(s)
  not in deck XOLD_RANK4, XOLD_RANK5
  not in file XNEW_DQ8  -> unplaced
```

**적용 결과를 반드시 숫자로 보고합니다.** layout 은 이름으로 맞추는 거라 다른 덱에서
만든 파일은 절반만 붙는데, 아무 말이 없으면 *파일이 깨진 것*처럼 보이지 *겨냥을 잘못한
것*으로는 안 보입니다. 깨진 JSON 도 조용히 넘어가지 않고 이유를 찍습니다.

- **`past --max-elements` 로 잘린 건 건드리지 않습니다.** 그건 누구의 배치도 아니라서,
  파일에 언급이 없다고 되살아나지도 않습니다.
- 파일에 없는 인스턴스는 CLI 와 똑같이 맨 오른쪽 `unplaced` 컬럼으로 갑니다.

> `--layout` (CLI) 은 **덱이 바뀌어 다시 그릴 때** 쓰고, `load layout…` 은 **지금 이
> 페이지에서** 쓰는 겁니다. HTML 은 덱 고칠 때마다 새로 생성되니, 배치가 계속 붙어
> 있게 하는 건 결국 `--layout` 입니다.

**드래그로 만든 배치와 재생성한 그림은 픽셀 단위로 같습니다.** 뷰어 안의 배치 계산은
파이썬 쪽과 같은 규칙(같은 컬럼 폭 누적, 같은 barycentre 정렬)을 그대로 따르게
돼 있습니다. 둘이 어긋나면 저장하는 순간 그림이 틀어져서 기능 자체가 무의미해집니다.

레이아웃 파일은 사람이 읽고 고칠 수 있는 JSON이고, 읽었다 다시 쓰면 바이트 단위로
같습니다 (버전 관리에 넣으라고).

```json
{
  "version": 1,
  "columns": [{ "name": "TX" }, { "name": "channel" }, { "name": "RX" }],
  "hidden": ["VTX0", "VTX1"],
  "hidden_nets": ["odt0"],
  "elements": {
    "XCH":  { "column": 1, "row": 0 },
    "XRX":  { "column": 2, "row": 0 },
    "XTX":  { "column": 0, "row": 0 }
  }
}
```

몇 가지 정해둔 것:

- **덱에 새로 생긴 인스턴스**는 파일에 없으니 맨 오른쪽 `unplaced` 컬럼에 모입니다.
  아무 데나 끼워 넣으면 못 보고 지나치기 때문입니다. 헤더에 개수도 찍힙니다.
- **비운 컬럼은 자리를 지킵니다.** 없애버리면 오른쪽 것들이 다 밀려서 배치가 흐트러집니다.
- **레이아웃 파일이 깨져 있으면 에러로 멈춥니다.** 조용히 자동 배치로 돌아가면
  손으로 맞춰둔 게 날아간 것처럼 보이니까요.
- `--layout` 에 준 파일이 아직 없으면 자동 배치로 그리고 그렇다고 알려줍니다.

### 덱이 클 때

기본으로 **연결이 많은 순으로 400개**까지만 그리고, 몇 개를 뺐는지 헤더에 적습니다
(`--max-elements N`, `0` 이면 상한 없음).

> 이 상한은 속도 문제가 아닙니다. 재보면 **소자 900개짜리도 로드 0.5초, 재배치 22ms**
> 입니다. 그냥 아무도 그릴 생각이 없었던 평탄화된 네트리스트에 대한 안전장치고,
> 그래서 실제로 검토하는 덱보다 한참 위에 있어야 합니다.

**자극원(V/I)은 이 상한에 잘 안 잘립니다.** 소스는 보통 신호 1개 + 레일이라 핀 수로
줄 세우면 덱에서 제일 하찮아 보이는데, 실제로는 신호 경로의 출발점입니다. 그래서
**예산의 절반까지는 소스를 먼저 지킵니다.** 절반을 넘기면 나머지는 평소대로 경쟁하는데,
소스가 전부 자리를 차지해 버리면 정작 그 사이에 아무것도 없는 그림이 되기 때문입니다.

**상한에 잘린 것도 `hidden…` 패널의 `past --max-elements` 에 목록으로 뜹니다.**
클릭해서 필요한 것만 되살릴 수 있습니다. 다만 이건 **layout 파일에 저장되지 않습니다** —
상한은 매번 다시 계산되는 것이라, 이번 실행에서 잘린 것을 파일에 박아두면 영구히
숨겨지기 때문입니다. 계속 보이게 하려면 `--max-elements` 를 올리세요. 수백 개짜리는 여기 내장 레이아웃으로 예쁘게 안 나오니
graphviz에 넘기세요:

```bash
primesim-dm graph top.sp --format dot -o top.dot
dot -Tpdf top.dot -o top.pdf        # graphviz 가 있는 환경에서
```

## 5. 상한은 기본으로 없습니다

| 옵션 | 기본값 | 주면 |
|---|---|---|
| `--max-depth N` | **무제한** | `.include` / `.lib` 를 N단계까지만 따라감 |
| `--max-elements N` | **무제한** | `graph` 가 연결 많은 순으로 N개만 그림 (V/I 소스 우선 보호) |

둘 다 "모르는 트리를 일단 막아보는" 용도입니다. 기본으로 걸어두면 **검사 범위가 조용히
줄어드는** 쪽이 더 위험해서 끄는 게 기본입니다. 실제로 잘렸을 때는 헤더에 찍힙니다:

```
depth cap: 3 file(s) hit --max-depth; their includes were not followed
```

```
400 least-connected element(s) not drawn - listed under 'past --max-elements'
```

범위를 줄이고 싶으면 `--max-depth` 보다 **`--opaque` / `--skip` 을 먼저** 쓰세요.
어느 파일을 왜 안 봤는지가 남습니다.

## 6. 한계 / 주의

- SPICE 문법 전체를 파싱하지 않습니다. `.subckt` / `.ends` / `.include` / `.lib` /
  `.global` / `.connect` / `.model ... S` 와 주석·연속행만 해석합니다.
- `.if` / `.elseif` / `.endif` 는 분기 판단을 안 하고 **전부 읽습니다.**
- `.subckt` **내부는 읽지 않습니다.** 연결성은 최상위만 봅니다. 내부에서 실제로 어떻게
  쓰이는지는 모릅니다.
- 시뮬레이터를 실행하지 않습니다. 덱을 읽는 데까지가 범위입니다.
- 못 읽은 줄은 `unparsed`, 프로필 밖 소자는 `off-profile` 로 **개수와 줄번호가 항상
  찍힙니다.** 검사 범위를 모르면 "문제 없음"에 의미가 없어서입니다.

## 7. 구조

```
primesim_dm/
  spice.py       .subckt 파싱, 주석/연속행, .lib 섹션 선택
  deck.py        덱 → 소자/노드 (프로필, include 추적, opaque/skip)
  check.py       구조 검사와 리포트, 터미네이션 생성
  graph.py       연결성 그림 + HTML 뷰어 (라이브러리 없음, file:// 로 열림)
  references.py  기준 패턴/옵션 파일 경로 ← 여기만 고치면 됩니다
  cli.py         lint / terminate / graph
tests/           unittest (표준 라이브러리), 220개
```

라이브러리로도 쓸 수 있습니다:

```python
from primesim_dm import check, deck, graph

dk = deck.read(["top.sp"], skip=["/pdk/"])        # profile="dm" 이 기본
findings = check.Checker(dk, si=True).run()
html = graph.render_html(graph.build(dk))
```
