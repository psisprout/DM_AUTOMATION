# DM_AUTOMATION

사내 AI agent(opencode)용 MCP tool 모음.

## current profile MCP

전류 파형(current profile)을 말로 다루는 tool 9개: 여러 양식(csv, PWL, SPICE raw, 스코프 파일, 엑셀, mat …) 불러오기,
더하기/빼기/이어붙이기, 시간·전류 scale, 반복, 자르기, 재샘플링, di/dt edge 정렬, 평균/active·idle 평균/burst 통계,
그래프, 파일 저장.

- 사용 설명서: [docs/current_profile_mcp.md](docs/current_profile_mcp.md)
- 설치: `python3 -m pip install --user -r requirements.txt` (+ 필요하면 `requirements-optional.txt`)
- opencode 연결: [opencode.example.json](opencode.example.json)
- 확인: `python3 test_mcp.py 2>/dev/null`, `python3 -m pytest -q tests`

```
MCP.py                       MCP 서버 (가이드 MCP_tool_addition.md 구조)
scripts/current_profile.py   tool이 실행하는 CLI 스크립트
scripts/cplib/               파일 양식 읽기 / 파형 연산 / 그래프
tests/                       자동 테스트와 샘플 파일 생성기
```
