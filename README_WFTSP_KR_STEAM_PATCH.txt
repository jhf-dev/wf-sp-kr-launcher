Wind Fantasy SP KR -> Steam TW 패치 런처
========================================

실행 파일:
  WFTSP_KR_Steam_Patch_GUI.exe

이 exe는 Python/Tk 런타임을 포함한 standalone 실행 파일입니다.
사용자 PC에 Python을 설치할 필요가 없습니다.

중요:
  - 이 패치/런처는 게임 리소스 파일을 직접 포함하지 않습니다.
  - 패치 적용에는 사용자가 보유한 기존 한국어판 Wind Fantasy SP 폴더가 필요합니다.
  - 한국어판 폴더 입력칸은 빈 값으로 시작하므로 직접 선택해야 합니다.
  - Steam 대만판 폴더는 Steam 설치 정보에서 자동으로 탐지합니다.
  - 이 도구는 team-jhf가 작성/관리하는 비공식 패치 런처입니다.
  - 위 표기는 런처와 패치 보조 코드에 대한 표시이며, 원본 게임 권리 주장이나 공식 제휴를 의미하지 않습니다.
  - 원본 게임 리소스의 권리는 각 원 권리자에게 있으며, 원본 또는 패치된 게임 데이터의 재배포 권리를 부여하지 않습니다.

사용 순서:
  1. WFTSP_KR_Steam_Patch_GUI.exe 실행
  2. 한국어판 Wind Fantasy SP 폴더 선택
  3. 자동 탐지된 Steam 대만판 Wind Fantasy SP 폴더 확인
     자동 탐지에 실패하면 직접 선택
  4. 패치 적용 클릭
  5. 게임 실행 클릭

적용 내용:
  - 한국어판 데이터 파일을 Steam 대만판 Win10 클라이언트에 적용
  - 엔딩/후반부 진행 불가 stage 버그픽스 자동 반영
  - FY성 나가기 man 버그픽스 자동 반영
  - Steam Win10판 wind.dll의 CP936 출력 보정을 CP949로 변경
  - Steam Win10판 wind.dll의 TextOutA 부분 문자열 길이 처리 보정
  - 게임 실행 버튼은 WindConfig를 거치지 않고 wf_sp_win10.exe를 직접 실행
  - 화면 모드와 해상도는 게임 폴더의 ddraw.dll 프록시와 wftsp_ddraw.ini로 반영
  - 창모드는 현재 모니터 해상도 이하의 4:3 프리셋만 선택 가능
  - 전체 창 모드(borderless)는 중앙 4:3 게임 영역과 좌우 pillarbox로 출력
  - 창모드/전체 창 모드에서 640x480 기준 마우스 입력/커서 좌표 보정과 포커스 복귀 BGM resume 보정을 적용
  - 창모드/전체 창 모드에서만 원본 전체화면용 자동 최소화 처리를 우회

주의:
  - 패치 적용 전 게임과 WindConfig를 종료하세요.
  - 한국어판 원본 폴더는 수정하지 않습니다.
  - Steam 대만판 원본 파일은 _wftsp_kr_patch_backup 폴더에 백업합니다.
  - 문제가 있으면 GUI의 TW 원본 복구 버튼을 사용할 수 있습니다.
  - 화면 옵션이 문제를 일으키면 TW 원본 복구로 런처가 만든 ddraw.dll/wftsp_ddraw.ini도 제거됩니다.
  - 보정만 끄려면 wftsp_ddraw.ini에서 input_fix=0, audio_focus_fix=0, inactive_window_spoof=0으로 바꿀 수 있습니다.
  - Windows 보안 또는 백신이 런처 실행을 막으면 패치 적용 중에만 차단을 해제하고, 적용 후 원래 설정으로 되돌리세요.

권리/라이선스 고지:
  - 이 런처와 패치 보조 코드는 team-jhf가 작성/관리하는 비공식 도구입니다.
  - 이 도구의 배포는 원본 게임에 대한 권리 주장, 사용 허가, 공식 지원 또는 원 권리자와의 제휴를 의미하지 않습니다.
  - Wind Fantasy, Wind Fantasy Tactics Special, 원본 실행 파일, 데이터, 이미지, 음악, 상표 및 관련 저작물은 각 원 권리자의 자산입니다.
  - standalone exe는 PyInstaller로 빌드되며, 제3자 런타임 구성 요소를 포함할 수 있습니다.
  - 배포 런타임 구성 요소와 소스 빌드 도구 라이선스 고지는 THIRD_PARTY_NOTICES.txt를 확인하세요.
