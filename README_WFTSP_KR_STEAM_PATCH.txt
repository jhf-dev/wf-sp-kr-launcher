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
  - 게임 실행 버튼은 WindConfig를 거치지 않고 wf_sp_win10.exe를 직접 실행
  - 화면 모드와 해상도는 게임 실행 전 HKCU\WindSP 설정으로 반영

주의:
  - 패치 적용 전 게임과 WindConfig를 종료하세요.
  - 한국어판 원본 폴더는 수정하지 않습니다.
  - Steam 대만판 원본 파일은 _wftsp_kr_patch_backup 폴더에 백업합니다.
  - 문제가 있으면 GUI의 TW 원본 복구 버튼을 사용할 수 있습니다.
  - Windows 보안 또는 백신이 런처 실행을 막으면 패치 적용 중에만 차단을 해제하고, 적용 후 원래 설정으로 되돌리세요.
