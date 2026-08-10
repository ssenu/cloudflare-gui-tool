# 확장 방안 (미구현 기능 문서)

1차 버전에서 제외했지만 나중에 추가할 수 있는 Cloudflare 기능들. 각 항목은 필요해지면 별도 스펙으로 발전시킨다.

## 1. Quick Tunnel (임시 URL)

**무엇**: 로그인·설정 없이 `cloudflared tunnel --url http://localhost:8000` 한 번으로 임시 `*.trycloudflare.com` URL을 발급받는 기능.

**언제 유용한가**: 개발 중인 서비스를 몇 분만 외부에 보여줄 때. 도메인/DNS 설정이 전혀 필요 없음.

**구현 방안**: 메인 화면에 "빠른 공유" 버튼 추가 → 포트만 입력 → 실행 → stdout에서 발급된 URL 파싱해 표시 + 클립보드 복사. 프로세스 관리는 기존 process_mgr 재사용. 구현 난이도 낮음.

**주의**: URL이 매번 바뀌고, Cloudflare가 안정성을 보장하지 않음 (프로덕션 부적합).

## 2. Cloudflare Access (Zero Trust 접근 보호)

**무엇**: 터널로 연 사이트에 이메일 OTP, Google 로그인 등 인증 관문을 걸어 아무나 접근하지 못하게 하는 기능.

**언제 유용한가**: 관리자 페이지, 개인 대시보드, SSH 웹 터미널 등 본인만 쓰는 서비스를 외부에 열 때 사실상 필수적인 보안 계층.

**구현 방안**: Cloudflare API 토큰(Access: Apps and Policies 권한) 등록 → 터널 카드에 "보호 설정" 버튼 → Access Application + Policy(허용 이메일 목록)를 API로 생성. API 스코프 관리와 UI가 커져서 난이도 중간.

## 3. DNS 레코드 조회/정리

**무엇**: `route dns`로 생긴 CNAME 레코드를 GUI에서 확인하고, 터널 삭제 시 남은 레코드를 함께 정리하는 기능.

**언제 유용한가**: 터널을 만들고 지우다 보면 대시보드에 죽은 CNAME이 쌓이는데, 현재 cloudflared CLI로는 DNS 레코드 삭제가 불가능해 수동 정리해야 함.

**구현 방안**: Cloudflare API 토큰(Zone: DNS Edit 권한) 등록 → 존 레코드 목록 조회 → 터널 ID(`<uuid>.cfargotunnel.com`)를 가리키는 CNAME 필터링 → 터널 삭제 플로우에 "DNS 레코드도 삭제" 체크박스 추가. 난이도 낮음~중간.

## 4. 기타 아이디어 메모

- **Windows 서비스 모드**: GUI 없이 부팅 시 터널 자동 시작 (`cloudflared service install`). 현재는 GUI 프로세스 관리 방식이라 GUI 종료 시 터널도 꺼짐.
- **트레이 아이콘**: 창을 닫아도 트레이에 상주하며 터널 유지, 우클릭 메뉴로 on/off.
- **메트릭 대시보드**: cloudflared `--metrics` 엔드포인트로 요청 수/지연시간 그래프 표시.
- **여러 원격 머신 동시 모니터링**: 현재는 한 번에 하나의 대상만 전환. 모든 머신의 터널 상태를 한 화면에 요약하는 뷰.
