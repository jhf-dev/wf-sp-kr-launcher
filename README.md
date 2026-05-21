# Wind Fantasy SP KR Steam Patch Launcher

`Wind Fantasy SP` 한국어판 데이터를 Steam 대만판 Windows 10 대응 클라이언트 위에 적용하는 패치 런처입니다.

이 저장소와 배포 패키지는 게임 리소스 파일을 직접 포함하지 않습니다. 패치를 적용하려면 사용자가 보유한 기존 한국어판 정식 발매본 폴더가 필요합니다.

## 주요 기능

- Steam 설치 정보를 읽어 `Wind Fantasy SP` Steam판 경로 자동 탐지
- 한국어판 폴더와 Steam 대만판 폴더를 GUI에서 직접 선택
- 한국어판 데이터 파일을 Steam 대만판 Win10 클라이언트에 적용
- 후반부/엔딩 구간 `stage` 진행 불가 버그 자동 보정
- FY성에서 `나가기`를 눌러도 마을로 돌아오는 `man` 버그 자동 보정
- Steam Win10판 `wind.dll`의 CP936 출력 보정을 CP949로 패치
- Steam 대만판 원본 파일 자동 백업 및 복구
- `wf_sp_win10.exe` 직접 실행 버튼 제공
- 창모드/전체 창 모드(borderless) 및 해상도 설정을 DirectDraw 프록시로 반영
- 선택적으로 `WindConfig.exe` 실행

## 다운로드/실행

배포용 실행 파일:

```text
WFTSP_KR_Steam_Patch_GUI.exe
```

이 실행 파일은 Python/Tk 런타임을 포함한 standalone 빌드입니다. 사용자 PC에 Python을 따로 설치할 필요가 없습니다.

## 사용 방법

1. `WFTSP_KR_Steam_Patch_GUI.exe`를 실행합니다.
2. `한국어판 폴더`는 빈 값으로 시작합니다. 사용자가 보유한 Wind Fantasy SP 한국어판 폴더를 직접 선택합니다.
3. `Steam 대만판 폴더`는 Steam 설치 정보에서 자동으로 채워집니다. 자동 탐지에 실패하면 Steam의 `Wind Fantasy SP` 폴더를 직접 선택합니다.
4. `패치 적용`을 누릅니다.
5. 패치 후 `게임 실행`을 누르면 `WindConfig.exe`를 거치지 않고 `wf_sp_win10.exe`가 직접 실행됩니다.
6. 설정 프로그램이 필요하면 `WindConfig 실행`을 사용합니다.

패치 적용 전에는 게임과 `WindConfig.exe`를 종료하세요.

## 화면 설정

Steam Win10 실행 파일은 `WindConfig`의 레지스트리 해상도 값을 실제 렌더링 경로에 반영하지 않고, `wf_sp_win10.exe` 내부에서 DirectDraw exclusive 640x480 모드를 직접 설정하는 것으로 확인했습니다.

그래서 런처의 화면 모드와 해상도 입력값은 대상 Steam 폴더에 다음 런타임 파일을 설치하는 방식으로 반영합니다.

- `ddraw.dll`: 32비트 DirectDraw 프록시
- `wftsp_ddraw.ini`: `windowed`, `borderless`, `fullscreen` 모드와 출력 크기 설정

지원 상태:

- `창모드`: 640x480 게임 화면을 지정한 창 크기로 스케일링합니다.
- `전체 창 모드`: 현재 모니터 크기에 맞춘 borderless 창으로 전환합니다.
- `전체화면`: 프록시는 로드되지만 원본 DirectDraw 전체화면 경로를 그대로 통과시킵니다.

이 기능은 기존 게임 리소스를 포함하지 않는 런타임 보조 DLL 방식입니다. 문제가 있으면 GUI의 `TW 원본 복구`로 런처가 만든 `ddraw.dll`/`wftsp_ddraw.ini`도 제거됩니다.

## 원본 보존

한국어판 폴더는 읽기 전용 소스로만 사용하며 수정하지 않습니다.

Steam 대만판 폴더의 기존 파일은 패치 적용 전에 다음 위치로 백업합니다.

```text
<Steam 대만판 폴더>\_wftsp_kr_patch_backup
```

문제가 생기면 GUI의 `TW 원본 복구` 버튼으로 백업된 원본을 되돌릴 수 있습니다.

## 적용 파일

한국어판에서 읽어 Steam 대만판에 적용하는 파일:

- `game.ini`
- `setup.ini`
- `bmp`
- `manbmp`
- `manani`
- `man`
- `stage`

다음 파일은 한국어판과 Steam 대만판이 동일한 규격/해시인지 확인만 하고 복사하지 않습니다.

- `map`
- `face`
- `mapbmp`
- `Wave`

## 진행 불가 버그 보정

런처는 한국어판 순정 폴더를 입력으로 받아 다음 보정을 메모리에서 적용한 뒤 Steam 대만판에 씁니다.

- `stage`: 후반부/엔딩 구간에서 잘못된 대사 데이터 포인터 때문에 진행이 멈추는 문제 보정
- `man`: FY성 `CITY20.BIN`의 나가기 액션 tail 값을 Steam 대만판 정상 흐름과 맞게 보정

즉 한국어판 원본 폴더가 이미 패치되어 있을 필요는 없습니다.

## Windows 11 보안 안내

Windows 보안 또는 백신이 런처 실행을 막으면, 패치를 적용하는 동안만 차단을 해제한 뒤 다시 원래 설정으로 되돌리세요.

패치가 끝난 뒤 게임은 Steam판 `wf_sp_win10.exe`를 직접 실행합니다.

## 개발자용 명령

상태 확인:

```powershell
python tooling\wftsp_steam_kr_launcher.py status --kr-root <KR폴더> --tw-root <TW폴더>
```

패치 적용:

```powershell
python tooling\wftsp_steam_kr_launcher.py apply --kr-root <KR폴더> --tw-root <TW폴더>
```

Win10 실행 파일 직접 실행:

```powershell
python tooling\wftsp_steam_kr_launcher.py launch-win10 --no-apply --tw-root <TW폴더>
```

해상도 지정 후 실행:

```powershell
python tooling\wftsp_steam_kr_launcher.py launch-win10 --no-apply --tw-root <TW폴더> --display-mode windowed --width 1280 --height 720
```

전체 창 모드 실행:

```powershell
python tooling\wftsp_steam_kr_launcher.py launch-win10 --no-apply --tw-root <TW폴더> --display-mode borderless
```

원본 복구:

```powershell
python tooling\wftsp_steam_kr_launcher.py restore --tw-root <TW폴더>
```

standalone exe 빌드:

```powershell
python tooling\build_ddraw_proxy.py
python -m PyInstaller --noconfirm --clean --noconsole --onefile --name WFTSP_KR_Steam_Patch_GUI --paths tooling tooling\wftsp_steam_kr_patch_gui.py
```

빌드 결과는 `dist\WFTSP_KR_Steam_Patch_GUI.exe`에 생성됩니다. 배포 패키지에는 exe와 함께 `payload\ddraw.dll`도 포함되어야 합니다.

## 포함 파일

- `WFTSP_KR_Steam_Patch_GUI.exe`: 배포용 standalone GUI 런처
- `WFTSP_KR_Steam_Patch_GUI.cmd`: exe 실행 편의 래퍼
- `tooling/wftsp_steam_kr_patch_gui.py`: GUI 코드
- `tooling/wftsp_steam_kr_launcher.py`: 패치 적용/복구/실행 핵심 로직
- `tooling/runtime/ddraw_proxy.cpp`: 창모드/전체 창 모드용 DirectDraw 프록시
- `tooling/build_ddraw_proxy.py`: `payload/ddraw.dll` 빌드 스크립트
- `tooling/wftsp_dialogue_pointer_patch.py`: 후반부/엔딩 진행 불가 보정 로직
- `tooling/wftsp_fy_city_exit_patch.py`: FY성 나가기 보정 로직
- `payload/ddraw.dll`: 런처가 Steam판 폴더에 복사하는 DirectDraw 프록시
- `analysis/wftsp_steam_kr_patch_notes.md`: 분석 메모
