# d4-fishing-watcher

Diablo IV 낚시 자동화 보조 프로그램입니다. 현재 핵심 흐름은 메인 윈도우에서 `낚시 시작`을 누를 때마다 Diablo IV 창을 확인하고, 낚시 위치를 새로 지정한 뒤 낚시 엔진을 실행하는 방식입니다.

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

`run.bat`도 같은 메인 윈도우 진입점을 실행합니다.

## Diablo IV 사전 설정

Diablo IV는 창 모드 또는 테두리 없는 창 모드로 실행하는 것을 권장합니다. 프로그램은 실행 시 Diablo IV 창을 찾고, 그 창 위에서 감지 영역을 선택한 뒤 해당 영역 안에서 START/Ready 상태를 감지합니다.

게임 내 조작키 설정에서 다음 항목을 확인하세요.

- 소셜 메뉴 호출 키: 기본 `3`
- 상호작용 / 줍기 키: 기본 `R`
- 낚싯대 회수 키: 기본 `E`

프로그램 설정창의 Diablo IV 게임 조작키 값은 게임 안에 실제로 지정한 키와 같아야 합니다. 이동, 상호작용, 기술 칸이 같은 키에 묶여 있으면 자동 낚시 흐름이 꼬일 수 있으므로 게임 설정에서 이동 / 상호작용 / 기술 칸 결합을 해제하고, 위 세 키를 서로 의도한 동작에 맞게 분리해 두세요.

## 기본 사용 흐름

1. Diablo IV를 창 모드 또는 테두리 없는 창 모드로 실행합니다.
2. 게임 내 조작키 설정에서 소셜 메뉴, 상호작용/줍기, 낚싯대 회수 키를 확인합니다.
3. `python -m app.main` 또는 `run.bat`로 D4 Fishing Watcher 메인 윈도우를 실행합니다.
4. 설정창에서 프로그램 단축키와 Diablo IV 게임 조작키가 게임 설정과 같은지 확인합니다.
5. Diablo IV 창을 낚시 가능한 위치에 둡니다.
6. `PageUp` 또는 `시작` 버튼을 누르면 Diablo IV 창을 확인합니다.
7. 창을 찾으면 Diablo IV 창 위에 영역 선택 UI가 표시됩니다.
8. 낚시 감지에 사용할 영역을 드래그해 선택하면 낚시 엔진이 실행됩니다.
9. 현재 상태, 오늘의 낚시, 전체 기록, 진행 정보를 확인합니다.
10. `PageDown` 또는 `중지` 버튼을 누르면 기존 엔진의 중지 플래그를 통해 현재 루프를 안전하게 빠져나옵니다.

`current_fishing_search_roi`는 파일이나 프로필에 저장하지 않습니다. 실행 중에는 선택한 ROI를 유지하지만, 다음 시작 시에는 이전 ROI를 자동 재사용하지 않고 반드시 새로 선택합니다. 단, 같은 실행 안에서 30초 timeout 후 pull and recast를 반복할 때는 ROI를 유지합니다.

## 단축키

기본 전역 단축키:

- 시작: `PageUp`
- 중지: `PageDown`

메인 윈도우의 `설정` 버튼에서 시작/중지 단축키와 Diablo IV 게임 조작키를 확인하고 변경할 수 있습니다. 메인 화면에는 키 요약을 길게 표시하지 않고 현재 상태와 통계를 중심으로 보여줍니다. 변경 사항은 저장 즉시 전역 listener와 낚시 엔진 런타임에 반영되고 `data/settings.json`에 저장되어 다음 실행에도 유지됩니다.

허용 키:

- `F1` ~ `F12`
- `A` ~ `Z`
- `0` ~ `9`
- `Num0` ~ `Num9`
- `Insert`, `Home`, `End`, `PageUp`, `PageDown`

조합키는 지원하지 않습니다. `ESC`, `Ctrl`, `Alt`, `Shift`, 방향키, Enter, Space, Tab, CapsLock, 마우스 버튼 등은 단축키로 저장할 수 없습니다. 단축키 등록에 실패해도 앱은 종료되지 않으며, 시작/중지 버튼으로 계속 조작할 수 있습니다.

## 현재 감지 흐름

1. 시작 시 Diablo IV 창 위에서 fishing search ROI를 수동 지정합니다.
2. 캐스팅 후 지정 ROI 안에서 찌 후보를 wide search합니다.
3. 후보를 찾으면 `active_bobber_roi`로 추적합니다.
4. 추적 실패 시 주변 확장 reacquire를 수행합니다.
5. reacquire가 계속 실패하면 지정된 search ROI 안에서 wide search합니다.
6. Ready는 template fallback을 유지하되, 기본적으로 HSV 색상 blob 연속 감지를 함께 사용합니다.
7. 30초 동안 Ready/Bite가 없으면 설정된 낚싯대 회수 키를 입력한 뒤 같은 ROI로 재캐스팅합니다.

## 설정 파일

사용자 설정은 `data/settings.json`에 저장됩니다. 파일이 없으면 기본값으로 동작하며, 기존 파일에 새 `game_keys` 섹션이 없어도 기본 게임 키가 적용됩니다.

예시:

```json
{
  "hotkeys": {
    "start_fishing": "PageUp",
    "stop_fishing": "PageDown"
  },
  "game_keys": {
    "social_menu": "3",
    "interact_pickup": "R",
    "reel": "E"
  }
}
```

일반 숫자키는 `0` ~ `9`, NumPad 숫자키는 `Num0` ~ `Num9`처럼 구분해서 저장합니다.

## 앱 아이콘

개발 실행에서는 `assets/icons/app-icon.ico`와 `assets/icons/app-icon-circle.png`, `assets/icons/app-icon-circle-32.png`, `assets/icons/app-icon-circle-16.png`를 Tk 창 아이콘과 헤더 아이콘으로 사용합니다. `assets/icons/app-icon.svg`는 원본 자산으로 유지하고, `assets/icons/app-icon-original.png`는 원본 SVG 경로를 투명 배경으로 렌더링한 정적 자산입니다. 원형 아이콘은 검은 원본 도형을 흰색 원형 배경 위에 올린 파생 자산입니다.

향후 PyInstaller EXE 자체 아이콘까지 적용하려면 `assets/icons/app-icon.ico`를 빌드 옵션의 아이콘 경로에 지정하면 됩니다.

## 통계

메인 화면에는 `data/fishing_stats.db`에 저장된 오늘의 낚시와 전체 기록을 표시합니다. 오늘의 낚시는 `daily_fishing_stats`의 현재 날짜 시도/성공/낚시 시간이고, 전체 기록은 `total_fishing_stats`의 총 시도/성공/총 낚시 시간입니다. 낚시 실행이 종료될 때 현재 세션의 시도/성공/실행 시간이 DB 값에 반영됩니다.

UI는 SQLite를 직접 조회하지 않고 낚시 엔진의 통계 스냅샷을 표시합니다. 실패 또는 미완료 횟수는 저장 기준이 별도로 없으므로 메인 화면에서 추정 계산하지 않습니다.

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
- `app/settings.py`: 사용자 설정 JSON 읽기/쓰기와 단축키 검증
- `app/config.py`: 런타임 설정값
- `ui/`: Tkinter 메인 윈도우
- `core/`: Diablo IV 창 탐지, 화면 캡처, 좌표 helper, 전역 단축키 listener
- `features/`: 낚시 실행 기능
- `features/fishing/worker.py`: 메인 UI와 낚시 엔진 사이의 실행 worker
- `features/fishing/engine.py`: 낚시 사이클, ROI 세션 상태, tracking/reacquire/timeout 흐름
- `features/fishing/detector.py`: template fallback 및 Ready HSV 색상 blob 감지
- `features/fishing/actions.py`: 키/마우스 입력 helper
- `templates/`: start/ready template fallback 이미지
- `assets/icons/`: 앱 원본 아이콘과 Tk 창/헤더용 원형 아이콘
- `data/`: 런타임 데이터 폴더. 개인 통계 DB는 Git에서 제외됩니다.
- `logs/`: 날짜별 실행 로그가 생성되는 런타임 폴더

## 로그

메인 화면에는 긴 실행 로그를 표시하지 않습니다. 사용자가 조치해야 하는 상태는 현재 상태 영역에 짧게 표시하고, 개발용 상세 로그와 예외 traceback은 날짜별 파일에 저장합니다.

- 저장 위치: `logs/YYYY-MM-DD.log`
- 인코딩: UTF-8
- 로그 레벨: DEBUG 이상
- 예: `logs/2026-06-16.log`

문제가 발생하면 해당 날짜의 로그 파일을 확인하세요. 앱을 같은 날짜에 다시 실행하면 같은 파일에 이어서 기록합니다.

## 검증

문법 검사와 import smoke test는 다음 명령으로 수행합니다.

```powershell
python -m compileall .
python -c "import app; import core; import features.fishing; import ui"
```

수동 확인 항목:

- `python -m app.main` 또는 `run.bat` 실행
- 기본 단축키 `PageUp`/`PageDown` 등록 여부 확인
- 단축키 설정창에서 허용 키/금지 키/동일 키 충돌 확인
- 단축키 설정창에서 일반 숫자와 NumPad 숫자가 구분되는지 확인
- Diablo IV 게임 조작키 변경 저장 후 다음 낚시 동작부터 반영되는지 확인
- 저장 후 `data/settings.json` 생성 및 재실행 유지 확인
- Diablo IV 미실행 상태에서 `시작` 클릭 후 실패 안내와 시작 버튼 재사용 가능 여부 확인
- Diablo IV 미실행 상태에서 시작 단축키 입력 후 같은 흐름 확인
- Diablo IV 실행 상태에서 `시작` 클릭 후 영역 선택 UI 표시 확인
- 영역 선택 취소 후 다시 시작 가능 여부 확인
- 영역 선택 완료 후 worker 실행 확인
- worker 실행 중 메인 UI가 멈추지 않는지 확인
- `중지` 버튼으로 실행 종료 및 버튼/상태 복구 확인
- 다음 시작 시 감지 영역을 다시 선택하는지 확인
- `logs/YYYY-MM-DD.log` 생성 및 이어쓰기 확인
- 별도 실행 상태 창이나 삭제된 실행 경로가 나타나지 않는지 확인

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
- `assets/icons/app-icon.svg`
- `assets/icons/app-icon-original.png`
- `assets/icons/app-icon-circle.png`
- `assets/icons/app-icon-circle-32.png`
- `assets/icons/app-icon-circle-16.png`
- `assets/icons/app-icon.ico`
- `data/.gitkeep`

Git에서 제외하는 항목:

- `.venv/`
- `__pycache__/`
- `debug/`, `data/debug/`
- `data/*.db`
- `data/settings.json`
- `logs/*.log`
- 빌드 산출물과 로컬 환경 파일

`data/fishing_stats.db`는 개인 실행 통계 파일이므로 저장소에는 올리지 않습니다. 파일이 없어도 실행 시 자동 생성됩니다.

## 개발 규칙

이 프로젝트는 root에 Python 실행 파일을 두지 않고, Python 코드는 `app/`, `core/`, `features/`, `ui/` 패키지 내부에서 관리합니다.

세부 구조, import, 타입 힌트, Pylance 경고 처리, 검증 규칙은 [CODING_RULES.md](./CODING_RULES.md)를 따릅니다.
