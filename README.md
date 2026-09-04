# DM_AUTOMATION

PrimeSim 시뮬레이션 자동화 도구 모음.

## primesim-dm

PrimeSim 덱을 자동으로 셋업해주는 CLI. 모델 파일에서 `.subckt` 포트를 읽어와
네이밍 룰로 자동 결선하고, 안 쓰는 노드를 터미네이션한 뒤 덱(`.sp`)과 결선
리포트를 생성합니다. Python 3.6+ 표준 라이브러리만 사용하며 pip 설치가 필요 없습니다.

```bash
cd primesim-dm
python3 -m unittest discover -s tests          # 137 tests
python3 -m primesim_dm gen examples/hbm_tx_rx.jsonc
```

자세한 사용법은 [`primesim-dm/README.md`](primesim-dm/README.md) 참고.

> 이 코드는 `Tent-fit-checker` 저장소의 `claude/primesim-dm-auto-setup-sogf51`
> 브랜치에서 커밋 이력을 그대로 옮겨온 것입니다.
