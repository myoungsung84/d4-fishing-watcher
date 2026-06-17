# D4 Fishing Watcher Coding Rules

## 1. 프로젝트 구조 원칙

이 프로젝트의 Python 코드는 root에 두지 않는다.

root에는 실행/문서/데이터 관련 파일만 둔다.

허용되는 root 항목 예시:

- README.md
- CODING_RULES.md
- requirements.txt
- run.bat
- app/
- core/
- features/
- ui/
- data/
- templates/

금지:

- root에 `main.py`, `config.py`, `detector.py`, `screen.py` 같은 Python 파일 생성
- 임시 테스트용 `.py` 파일을 root에 생성
- 구조 정리 이전의 legacy 파일명을 root에 재생성
- 새 기능 구현을 위해 root에 Python 파일을 추가하는 작업

기본 GUI 실행:

```bash
python -m app.main
```

## 2. 패키지 역할

### app

애플리케이션 실행 진입점과 실행 구성을 담당한다.

예:

- GUI 실행 진입점
- 앱 초기화

### core

공통 기반 로직을 담당한다.

예:

- 공통 타입
- 공통 설정
- 공통 유틸
- 공통 상태 모델
- 전역 단축키 listener 관리

단, 낚시 기능에만 종속되는 도메인 로직은 core로 올리지 않는다.

### features/fishing

낚시 기능 도메인 로직을 담당한다.

예:

- 낚시 감지 로직
- 낚시 루프
- 낚시 cycle
- Diablo IV 창/ROI/좌표 처리
- 클릭 시나리오
- engine runtime

### ui

GUI 화면과 UI worker를 담당한다.

예:

- 메인 윈도우
- 버튼/로그/상태 표시
- FishingWorker
- 사용자 입력 이벤트

UI는 낚시 세부 로직을 직접 구현하지 않는다.  
UI는 engine을 호출하고 상태를 표시하는 역할만 가진다.

권장 흐름:

```text
MainWindow
  -> FishingWorker
    -> features.fishing.engine
```

## 3. import 규칙

numpy와 cv2는 명시 import를 사용한다.

권장:

```python
import numpy as np
import cv2
```

금지:

```python
np = LazyModule("numpy")
cv2 = LazyModule("cv2")
```

pyautogui도 LazyModule로 재도입하지 않는다.

이유:

- Pylance가 `np.ndarray` 타입 힌트를 정상 타입으로 인식하지 못할 수 있음
- `reportInvalidTypeForm` 경고 발생 가능
- 타입 추론과 유지보수성이 떨어짐
- 호출 흐름에서 `object`, `Any`로 타입이 흐려질 수 있음

## 4. 타입 힌트 규칙

이미지 배열 타입은 기본적으로 다음 형태를 사용한다.

```python
import numpy as np

def detect(image: np.ndarray) -> bool:
    ...
```

캡처, 변환, crop, detect 함수는 가능한 한 인자/반환 타입을 명시한다.

예:

```python
def capture_screen_bgr(...) -> np.ndarray:
    ...

def get_ready_candidate_crop(screen_bgr: np.ndarray, ...) -> np.ndarray | None:
    ...
```

문자열 타입 힌트는 순환 import 문제가 실제로 있을 때만 사용한다.

지양:

```python
def detect(image: "np.ndarray") -> bool:
    ...
```

Pylance 경고를 숨기기 위한 목적으로 타입 힌트를 문자열 처리하지 않는다.

### Optional 타입 좁히기 규칙

Pylance는 객체 속성의 Optional 타입 좁히기를 보수적으로 판단할 수 있다.

따라서 `obj.attr is not None`을 확인한 직후라도, 이후 함수 호출이나 다른 접근에서 `obj.attr`을 여전히 `T | None`으로 볼 수 있다.

Optional 속성을 non-Optional 인자에 전달해야 할 때는 속성값을 로컬 변수로 먼저 받은 뒤 None 분기한다.

권장:

```python
center = result.screen_center
if center is None:
    return None

candidate = BobberCandidate(center=center)
```

권장:

```python
active_bobber_roi = tracking.active_bobber_roi
if active_bobber_roi is None:
    return None

expanded_roi = expand_roi(active_bobber_roi)
```

지양:

```python
if result.screen_center is not None:
    candidate = BobberCandidate(center=result.screen_center)
```

지양:

```python
if tracking.active_bobber_roi is not None:
    expanded_roi = expand_roi(tracking.active_bobber_roi)
```

처리 기준:

- `tuple[int, int] | None` 값을 `tuple[int, int]` 인자에 바로 전달하지 않는다.
- `tuple[int, int, int, int] | None` 값을 `tuple[int, int, int, int]` 변수/인자에 바로 할당하지 않는다.
- ROI, center, crop, window rect, template 등 실패 가능성이 있는 값은 Optional로 표현한다.
- Optional 값을 사용할 때는 호출 전에 명확히 None 분기한다.
- 좌표/ROI 기본값을 임의로 넣어 타입 경고를 없애지 않는다.
- `cast()`로 Optional을 덮지 않는다.
- 타입 힌트를 문자열로 바꿔 경고를 숨기지 않는다.
- 실패 가능성이 실제로 없는 값이면 원천 함수의 반환 타입을 non-Optional로 바로잡는다.
- 실패 가능성이 실제로 있는 값이면 호출부에서 분기한다.

## 5. Pylance 경고 처리 원칙

Pylance 경고는 사용자가 개별 오류 메시지를 하나씩 전달해서 고치는 방식으로 처리하지 않는다.

Codex는 작업 시점의 VS Code/Pylance Problems 기준으로 남아 있는 타입 경고를 직접 확인하고, 관련 파일과 호출 흐름을 스스로 추적해 정리한다.

VS Code/Pylance Problems와 CLI pyright 결과가 다를 수 있으므로, 최종 확인은 로컬 VS Code Problems 기준으로 한다.

CLI에서 진단 출력이 없어도 VS Code Problems에 남는 경고가 있으면 완료로 보지 않는다.

### Python 타입 검사 기준

`python -m compileall`은 문법 검사이며 타입 검사가 아니다.

import smoke test는 import 및 초기 로딩 검사이며 타입 검사가 아니다.

Pylance 오류 확인이 필요한 작업은 반드시 Pyright 계열 검사 결과 또는 VS Code/Pylance Problems의 실제 진단 내용을 기준으로 판단한다.

타입 오류 정리 작업을 `compileall` 성공만으로 완료 처리하지 않는다.

import smoke test 성공을 타입 검사 성공으로 보고하지 않는다.

VS Code Problems에 error 수준 진단이 남아 있으면 완료로 보고하지 않는다.

타입 오류 작업을 시작할 때는 코드를 수정하기 전에 실제 진단 목록을 먼저 작성한다.

진단 목록에는 다음을 포함한다.

- 파일
- 줄 번호
- Pylance/Pyright 오류 코드
- 전체 오류 메시지
- 실제 원인

오류 개수만 보고 추측성으로 수정하지 않는다.

사용자가 개별 경고 메시지를 전달해야만 수정되는 방식은 실패로 간주한다.

Codex는 전체 타입 흐름을 보고 같은 패턴의 Optional 경고를 함께 찾아야 한다.

Pylance 경고를 처리할 때는 해당 라인만 임시 수정하지 않는다.  
값이 생성되는 원천 함수부터 호출 흐름을 따라가며 타입이 흐려지는 지점을 정리한다.

확인 기준:

- 타입 힌트가 실제 런타임 값과 맞는지 확인한다.
- 함수 반환 타입이 호출부 인자 타입과 이어지는지 확인한다.
- 이미지 캡처, 변환, crop, detect 흐름에서 타입이 `object` 또는 `Any`로 흐려지는 지점을 확인한다.
- `None` 가능성이 있는 값은 호출 전에 명확히 분기 처리한다.
- LazyModule 때문에 타입 힌트가 깨지는 구조가 없는지 확인한다.
- 사용자가 전달한 단일 경고 메시지에 의존하지 않고, 같은 타입 흐름에 있는 파일과 함수를 함께 점검한다.

이미지 처리 흐름은 가능한 한 다음 타입을 유지한다.

```python
screen_bgr: np.ndarray
crop: np.ndarray
mask: np.ndarray
```

예상 흐름:

```text
screen capture
  -> BGR 변환
    -> ROI crop
      -> ready/bite detect
```

호출부에서 `cast()`를 남발하지 않는다.

`cast()`는 외부 라이브러리 반환 타입이 부정확하고, 런타임에서 타입이 확실히 보장되는 경우에만 제한적으로 사용한다.

나쁜 방향:

```python
from typing import cast

get_ready_candidate_crop(cast(np.ndarray, screen_bgr))
```

좋은 방향:

```python
def capture_screen_bgr(...) -> np.ndarray:
    screen_bgr: np.ndarray = ...
    return screen_bgr
```

즉, 호출부 땜빵보다 원천 함수의 반환 타입을 바로잡는다.

Pylance 경고 처리 완료 보고에는 다음을 포함한다.

- Codex가 확인한 경고 유형 요약
- 작업 전 실제 진단 목록
- 타입 흐름상 원인이 된 파일/함수
- 호출부 임시 처리 여부
- 원천 함수 타입 정리 여부
- 실행한 Pyright 계열 타입 검사 명령
- 남은 경고 여부

## 6. LazyModule 사용 기준

LazyModule은 기본적으로 새로 추가하지 않는다.

특히 다음 모듈에는 사용하지 않는다.

- numpy
- cv2
- pyautogui

성능이나 시작 속도 문제로 지연 로딩이 필요하면 먼저 구조상 필요한지 검토하고, 타입 힌트와 충돌하지 않는 방식으로 제한적으로 적용한다.

## 7. 기능 변경 금지 영역

구조 정리, import 정리, 타입 경고 수정 작업에서는 다음을 변경하지 않는다.

- 낚시 감지 로직
- ROI 범위
- 좌표 계산
- 클릭 위치
- 클릭 순서
- sleep 값
- cast/reel/recast 타이밍
- Ready/Bite/Reel/Recast 동작
- templates 이미지 기준
- 실제 마우스/키보드 동작 타이밍

타입 경고 수정 작업은 타입/import 정리에 한정한다.

## 8. GUI와 engine 연결 원칙

GUI는 engine 내부 구현을 직접 알지 않도록 한다.

권장 흐름:

```text
MainWindow
  -> FishingWorker
    -> fishing engine
      -> fishing cycle
```

`FishingWorker`는 시작/중지 lifecycle과 상태 전달을 담당한다.  
실제 낚시 판단과 클릭 시나리오는 `features/fishing` 내부에 둔다.

GUI worker는 메인 윈도우의 시작/중지 흐름에 연결된 상태를 유지한다.

## 9. 메인 윈도우 실행 원칙

공식 실행 진입점은 메인 윈도우로 유지한다.

다음 명령이 계속 동작해야 한다.

```bash
python -m app.main
```

낚시 엔진을 변경하더라도 메인 윈도우의 시작/중지 worker 흐름이 깨지면 안 된다.

## 10. 전역 단축키 원칙

전역 단축키 listener는 core 계층에서 관리한다.

- UI, Worker, Engine에서 pynput listener를 직접 생성하지 않는다.
- listener callback은 Tkinter widget을 직접 수정하지 않고 UI queue나 callback을 통해 UI 스레드로 전달한다.
- 시작/중지 단축키 동작은 메인 윈도우의 기존 버튼 핸들러를 재사용한다.
- 사용자 단축키 설정은 config.py에 하드코딩하지 않고 별도 사용자 설정 파일에 저장한다.

## 11. 검증 명령

타입 오류 정리 작업에서는 타입 검사를 가장 먼저 실행한다.

프로젝트에 `pyright` 또는 `basedpyright`가 설치되어 있으면 해당 CLI를 사용한다.

설치되어 있지 않으면 런타임 의존성에 임의로 추가하지 않는다.

현재 프로젝트에서는 일회성 검사로 다음 명령을 사용할 수 있다.

```powershell
pnpm dlx pyright@latest ui --pythonversion 3.9 --pythonplatform Windows
```

검사 범위가 UI가 아니면 마지막 경로 인자를 작업 범위에 맞게 바꾼다.

Pylance/Pyright 설정 파일이 있으면 타입 검사 전에 확인한다.

확인 대상:

- pyrightconfig.json
- pyproject.toml
- .vscode/settings.json
- requirements.txt
- requirements-dev.txt

특히 다음 설정이 있는지 확인한다.

- pythonVersion
- pythonPlatform
- typeCheckingMode
- extraPaths
- venvPath
- venv
- reportUnknownMemberType
- reportUnknownArgumentType
- reportOptionalMemberAccess
- reportArgumentType
- reportAssignmentType
- reportReturnType
- reportGeneralTypeIssues

VS Code가 사용하는 Python 인터프리터와 검증 명령의 인터프리터가 같은지도 확인한다.

현재 프로젝트 인터프리터:

```powershell
.\.venv\Scripts\python.exe
```

문법 검사:

```bash
python -m compileall .
```

`compileall`은 타입 검사가 아니며, Pylance/Pyright 오류 해결 여부를 판단하는 기준으로 사용하지 않는다.

import smoke test:

```bash
python -c "import app"
python -c "import app.main"
python -c "import core"
python -c "import features.fishing"
python -c "import ui"
```

import smoke test는 import 및 초기 로딩 검사이며, Pylance/Pyright 오류 해결 여부를 판단하는 기준으로 사용하지 않는다.

root Python 파일 확인:

```powershell
Get-ChildItem -File -Filter *.py
```

LazyModule 잔여 확인:

```bash
rg "LazyModule"
```

numpy 타입 힌트 확인:

```bash
rg "np\.ndarray"
```

Pylance 경고는 `compileall`로 확인되지 않는다.  
VS Code Problems 탭 기준으로 별도 확인한다.

## 12. 작업 보고 규칙

작업 완료 보고는 아래 형식을 따른다.

```report
[작업 제목]

1. 작업 목표
- 요약:

2. 변경 파일
- 수정:
- 추가:
- 삭제:

3. 주요 변경 내용
-

4. 보존한 동작
- Python 코드 변경 여부:
- 낚시 감지 로직 변경 여부:
- 좌표/ROI 변경 여부:
- 클릭 타이밍 변경 여부:
- 메인 윈도우 시작/중지 흐름 변경 여부:

5. 검증 결과
- Pyright/Pylance 타입 검사:
- python -m compileall .:
- import smoke test:
- root *.py 존재 여부:
- LazyModule 잔여 여부:

6. 남은 이슈
-

7. 다음 작업 제안
-
```
