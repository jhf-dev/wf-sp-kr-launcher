# Wind Fantasy SP KR Steam Patch Launcher

`Wind Fantasy SP` 한국어판 데이터를 Steam 대만판 Windows 10 대응 클라이언트 위에 적용하는 패치 런처입니다.

한국어판 순정 데이터에 남아 있는 진행 불가 구간도 런처가 자동으로 보정합니다.

## 주요 기능

- 한국어판 폴더와 Steam 대만판 폴더를 GUI에서 직접 선택
- 한국어판 데이터 파일을 Steam 대만판 Win10 클라이언트에 적용
- 후반부/엔딩 구간 `stage` 진행 불가 버그 자동 보정
- FY성에서 `나가기`를 눌러도 마을로 돌아오는 `man` 버그 자동 보정
- Steam Win10용 `wind.dll`의 CP936 출력 보정을 CP949로 패치
- Steam 대만판 원본 파일 자동 백업 및 복구
- 선택적으로 `WindConfig.exe` 실행

## 다운로드/실행

배포용 실행 파일:

```text
WFTSP_KR_Steam_Patch_GUI.exe
```

이 실행 파일은 Python/Tk 런타임을 포함한 standalone 빌드입니다. 사용자 PC에 Python을 설치할 필요가 없습니다.

## 사용 방법

1. `WFTSP_KR_Steam_Patch_GUI.exe`를 실행합니다.
2. `한국어판 폴더`에 Wind Fantasy SP 한국어판 폴더를 선택합니다.
3. `Steam 대만판 폴더`에 Steam판 Wind Fantasy SP 폴더를 선택합니다.
4. `패치 적용`을 누릅니다.
5. 필요하면 `WindConfig 실행` 또는 `적용 후 WindConfig 실행`을 사용합니다.

패치 적용 전에는 게임과 `WindConfig.exe`를 종료하세요.

## 원본 보존

한국어판 폴더는 읽기 전용 소스로만 사용하며 수정하지 않습니다.

Steam 대만판 폴더의 기존 파일은 패치 적용 전에 다음 위치로 백업됩니다.

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

다음 파일은 한국어판과 Steam 대만판이 동일한 규격/해시로 확인되어 복사하지 않습니다.

- `map`
- `face`
- `mapbmp`
- `Wave`

## 진행 불가 버그 보정

런처는 한국어판 순정 폴더를 입력으로 받아도 다음 보정을 메모리에서 적용한 뒤 Steam 대만판에 씁니다.

- `stage`: 후반부/엔딩 구간에서 잘못된 대사 데이터 때문에 진행이 멈추는 문제 보정
- `man`: FY성 `CITY20.BIN`의 나가기 액션 tail 값을 Steam 대만판 정상 흐름과 맞게 보정

즉 한국어판 원본 폴더가 이미 패치되어 있을 필요는 없습니다.

## Windows 11 보안 안내

테스트 환경에서는 Windows 11 Smart App Control과 NAS 경유 다른 PC 실행 모두 별도 경고 없이 통과했습니다.

다만 보안 프로그램이나 Smart App Control 정책은 PC마다 다를 수 있습니다. 패치 런처 실행 또는 패치 적용이 차단되는 경우, 패치 적용 중에만 보안 정책을 일시적으로 완화한 뒤 게임 실행까지 확인하고 다시 원래 설정으로 돌리는 것을 권장합니다.

## 개발자용 명령

상태 확인:

```powershell
python tooling\wftsp_steam_kr_launcher.py status --kr-root <KR폴더> --tw-root <TW폴더>
```

패치 적용:

```powershell
python tooling\wftsp_steam_kr_launcher.py apply --kr-root <KR폴더> --tw-root <TW폴더>
```

원본 복구:

```powershell
python tooling\wftsp_steam_kr_launcher.py restore --tw-root <TW폴더>
```

standalone exe 빌드:

```powershell
python -m PyInstaller --noconfirm --clean --noconsole --onefile --name WFTSP_KR_Steam_Patch_GUI --paths tooling tooling\wftsp_steam_kr_patch_gui.py
```

빌드 결과는 `dist\WFTSP_KR_Steam_Patch_GUI.exe`에 생성됩니다.

## 포함된 파일

- `WFTSP_KR_Steam_Patch_GUI.exe`: 배포용 standalone GUI 런처
- `WFTSP_KR_Steam_Patch_GUI.cmd`: exe 실행 편의 래퍼
- `tooling/wftsp_steam_kr_patch_gui.py`: GUI 코드
- `tooling/wftsp_steam_kr_launcher.py`: 패치 적용/복구 핵심 로직
- `tooling/wftsp_dialogue_pointer_patch.py`: 후반부/엔딩 진행 불가 보정 로직
- `tooling/wftsp_fy_city_exit_patch.py`: FY성 나가기 보정 로직
- `analysis/wftsp_steam_kr_patch_notes.md`: 분석 메모
