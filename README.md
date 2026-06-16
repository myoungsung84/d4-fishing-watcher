# d4-fishing-watcher

Diablo IV 낚시 자동화 보조 프로그램입니다. 현재 핵심 흐름은 `PageUp`으로 이번 낚시터의 탐색 영역을 직접 드래그 지정하고, 지정된 ROI 안에서 Ready 아이콘의 민트/청록 HSV 색상 blob을 연속 프레임으로 확인하는 방식입니다.

## 주의사항

이 프로젝트는 개인용 비공식 도구입니다. 게임 서비스 약관, 자동화 정책, 계정 제재 가능성은 사용자가 직접 확인하고 책임져야 합니다.

## 실행

권장 환경은 Python 3.9 이상입니다.

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m app.main
```

`run.bat`도 현재는 GUI를 실행하지만, 앞으로의 기본 UX는 배치 파일이 아니라 메인 윈도우입니다.

기존 콘솔/hotkey 중심 실행은 비교 기준으로 유지하며, 패키지 entrypoint로 실행할 수 있습니다.

```powershell
python -m app.legacy_console
```

## 단축키

- `PageUp`: 낚시터 탐색 영역을 드래그 지정하고 자동 낚시 시작
- `PageDown`: 자동 낚시 중단
- `ESC` / `F12`: 자동 낚시 중단
- `Ctrl+C`: 프로그램 종료

`current_fishing_search_roi`는 파일이나 프로필에 저장하지 않습니다. PageUp으로 지정한 ROI는 현재 실행 세션에서만 유지되며, PageDown/ESC/F12 중단이나 프로그램 종료 시 폐기됩니다. 단, 같은 자리에서 30초 timeout 후 pull and recast를 반복할 때는 ROI를 유지합니다.

## 현재 감지 흐름

1. PageUp으로 fishing search ROI를 수동 지정합니다.
2. 캐스팅 후 지정 ROI 안에서 찌 후보를 wide search합니다.
3. 후보를 찾으면 `active_bobber_roi`로 추적합니다.
4. 추적 실패 시 주변 확장 reacquire를 수행합니다.
5. reacquire가 계속 실패하면 다시 PageUp으로 지정한 search ROI 안에서 wide search합니다.
6. Ready는 template fallback을 유지하되, 기본적으로 HSV 색상 blob 연속 감지를 함께 사용합니다.
7. 30초 동안 Ready/Bite가 없으면 reel_key를 입력한 뒤 같은 ROI로 재캐스팅합니다.

## Ready 색상 감지

기본 HSV 범위:

```python
READY_COLOR_HSV_LOWER = (70, 60, 120)
READY_COLOR_HSV_UPPER = (95, 255, 255)
```

오탐이 많으면 `app/config.py`에서 더 좁은 범위로 조정할 수 있습니다.

```python
READY_COLOR_HSV_LOWER = (80, 90, 160)
READY_COLOR_HSV_UPPER = (92, 255, 255)
```

색상 감지는 단독 1프레임으로 확정하지 않고 `READY_COLOR_MIN_FRAMES` 이상의 연속 hit가 있을 때만 Ready 후보로 인정합니다.

## 폴더 구조

- `app/`: GUI 앱 진입점, 상태 enum, 경로 helper, 공용 logger
- `app/config.py`: 런타임 설정값
- `app/legacy_console.py`: 기존 콘솔/hotkey 실행 호환 entrypoint
- `ui/`: Tkinter 메인 윈도우
- `ui/overlay.py`: 기존 Tkinter overlay와 ROI 선택 UI
- `core/`: Diablo IV 창 탐지, 화면 캡처, 좌표 helper
- `features/`: 향후 낚시 실행 엔진 분리 위치
- `features/fishing/engine.py`: legacy 콘솔 낚시 루프, hotkey, ROI 세션 상태, tracking/reacquire/timeout 흐름
- `features/fishing/detector.py`: template fallback 및 Ready HSV 색상 blob 감지
- `features/fishing/actions.py`: 키/마우스 입력 helper
- `templates/`: start/ready template fallback 이미지
- `data/`: 런타임 데이터 폴더. 개인 통계 DB는 Git에서 제외됩니다.

## 검증

문법 검사는 다음 명령으로 수행합니다.

```powershell
python -m compileall .
python -c "import app.main; import ui.main_window"
```

이 프로그램은 실제 게임 화면, ROI 지정, HSV 색상 감지, Tkinter overlay, 키 입력 동작이 핵심이라 수동 검증이 중요합니다.

수동 확인 항목:

- `python -m app.main` 또는 `run.bat` 실행
- 필요 시 `python -m app.legacy_console` 실행
- `PageUp` ROI 드래그 후 자동 시작
- READY DEBUG에서 Ready color hits 표시 확인
- `PageDown` 중단
- `ESC` / `F12` 중단
- 30초 timeout 후 reel/recast 확인

## Tkinter Overlay 주의

Tkinter 객체는 `ui/overlay.py`의 overlay UI thread에서만 생성/갱신/삭제합니다. 외부 스레드는 `OverlayController` queue에 command만 넣습니다. PageUp ROI 선택 오버레이도 새 `Tk()`를 만들지 않고 기존 root의 `Toplevel`로 생성합니다.

## 튜닝 포인트

- Ready 색상 오탐: `ready_color_hsv_lower`, `ready_color_hsv_upper`, `ready_color_min_area`, `ready_color_max_area`
- Ready 연속 프레임: `ready_color_min_frames`
- 찌 추적: `bobber_track_fail_limit`, `bobber_reacquire_fail_limit`, `bobber_track_padding`, `bobber_reacquire_padding`
- timeout/recast: `bobber_search_timeout_sec`, `ready_timeout_reel_wait`

## Git 관리 기준

Git에 포함하는 항목:

- Python 소스 파일
- `requirements.txt`, `run.bat`, `README.md`, `.gitignore`
- `templates/fishing_start_icon.png`
- `templates/fishing_ready_icon.png`
- `data/.gitkeep`

Git에서 제외하는 항목:

- `.venv/`
- `__pycache__/`
- `debug/`, `data/debug/`
- `data/*.db`
- 빌드 산출물과 로컬 환경 파일

`data/fishing_stats.db`는 개인 실행 통계 파일이므로 저장소에는 올리지 않습니다. 파일이 없어도 실행 시 자동 생성됩니다.
