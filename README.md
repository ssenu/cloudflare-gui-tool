# Cloudflare Tunnel GUI

cloudflared 터널을 터미널 명령 대신 GUI로 관리하는 데스크톱 앱.

## 기능
- 터널 목록 / 생성 마법사 (create + route dns + config.yml 자동)
- 터널 on/off, 웹서버(uvicorn 등) 동시 실행, 단축키 (Ctrl+1~9)
- 실시간 로그 뷰어
- SSH 원격 모드: 라즈베리파이의 cloudflared를 같은 화면에서 제어

## 실행
```bash
pip install -r requirements.txt
python -m app.main
```

## 테스트
```bash
pytest
```

문서: `docs/superpowers/specs/`(스펙), `docs/future-extensions.md`(확장 방안)
