# Cloudflare Tunnel GUI

cloudflared 터널을 터미널 명령 대신 GUI로 관리하는 데스크톱 앱.

## 기능
- 터널 목록 / 생성 마법사 (create + route dns + config.yml 자동)
- **상시 실행**: 터널·서버는 GUI가 아니라 대상 머신에서 독립 프로세스로 돈다. 앱을 껐다 켜도, 노트북을 재부팅해도 실행 중인 터널/서버는 그대로 유지되고 GUI는 다시 켜졌을 때 상태만 읽어와 화면에 이어서 보여준다(창을 닫아도 아무것도 멈추지 않는다).
- **한 터널에 여러 라우트**: 터널 하나에 서브도메인(라우트)을 여러 개 등록해 각각 독립된 서버로 연결할 수 있다. 터널 토글과 각 라우트(서버)의 토글은 서로 영향을 주지 않는다.
- **컨테이너 서비스 등록**: 라우트의 서버 종류로 "명령" 대신 "도커 컴포즈"를 선택하면 `docker compose up -d` / `down`으로 시작·정지하고, `docker compose ps`로 실행 상태를 판정한다.
- 라우트 추가/편집 후에는 터널 재시작이 필요하면 안내 배너를 띄운다.
- 웹서버(uvicorn 등) 동시 실행, 단축키 (Ctrl+1~9)
- 실시간 로그 뷰어
- SSH 원격 모드: 라즈베리파이 등 원격 대상의 cloudflared/서비스를 같은 화면에서 제어. SSH 연결이 끊겼다 다시 붙어도 원격에서 계속 돌던 프로세스 상태를 그대로 읽어온다.
- 라이트/다크 테마 (설정에서 전환)
- 상단 `?` 버튼: 전체 사용 흐름 안내

## 실행
```bash
pip install -r requirements.txt
python -m app.main
```

## 테스트
```bash
pytest
```

## 실행 파일(.exe) 빌드
```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name "Cloudflare Tunnel GUI" --icon assets/cloudflare_logo.ico --add-data "assets;assets" app/main.py
```
결과물은 `dist\Cloudflare Tunnel GUI.exe`에 생성된다(단일 실행 파일, 콘솔 창 없음). 저장소에 커밋된 `Cloudflare Tunnel GUI.spec`으로 같은 옵션을 재현할 수 있다(`pyinstaller "Cloudflare Tunnel GUI.spec"`).

문서: `docs/superpowers/specs/`(스펙), `docs/manual-test-checklist.md`(수동 테스트 체크리스트), `docs/future-extensions.md`(확장 방안)
