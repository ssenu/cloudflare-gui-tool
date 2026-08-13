# 라즈베리파이를 Railway처럼 쓰기 — webPi 세팅 가이드

Ubuntu Server를 올린 Raspberry Pi 5(장치 이름 `webPi`, 사용자 `admin`)를 개인 배포 서버로 만드는 순서다. 다 끝나면 이렇게 된다.

- 집 밖 어디서든 노트북에서 `ssh admin@webpi` 로 접속 (Tailscale)
- 이 저장소의 GUI 앱에서 대상을 **webPi**로 바꾸면 원격 터널·서버를 그대로 조작
- GitHub 저장소를 여러 개 올려두고, 토글 하나로 최신 코드 받아 다시 빌드·기동
- 각 프로젝트는 `https://<서브도메인>.<내도메인>` 으로 외부 공개 (Cloudflare Tunnel)

```
   인터넷 ──► Cloudflare ──(터널)──► webPi ──► docker compose (프로젝트별 컨테이너)
   노트북 ──► Tailscale ──(SSH)───► webPi ──► GUI 앱이 원격 조작
```

> 표기: `<...>` 는 본인 값으로 바꿀 자리. 명령 앞의 `pi$` 는 라즈베리파이에서, `pc>` 는 노트북에서 실행한다는 뜻이다.

---

## 0. 준비물 확인

- webPi가 랜선으로 공유기에 연결되어 있고 전원이 켜져 있음
- 노트북이 같은 공유기(와이파이)에 붙어 있음
- Cloudflare에 도메인이 등록되어 Active 상태 (앱에서 쓰던 그 도메인)
- GitHub 계정과 올릴 프로젝트 저장소

---

## 1. 첫 접속과 기본 세팅

### 1-1. IP 찾기

공유기 관리 페이지의 DHCP 클라이언트 목록에서 `webPi` 항목의 IP를 확인한다. 안 보이면 노트북에서:

```powershell
pc> arp -a | findstr "192.168"
```

**이 집 환경에서 확인된 값** (Linksys E9450, 관리 주소 `http://192.168.79.1`):

| 항목 | 값 |
|---|---|
| 호스트 이름 | webPi |
| MAC 주소 | `d8:3a:dd:11:22:33` |
| IP 주소 | **192.168.79.6** (DHCP 예약으로 고정 완료) |

아래에서 `<PI_IP>` 는 `192.168.79.6` 이다.

### 1-1-1. IP 고정 (이미 적용됨)

공유기가 매번 같은 주소를 주도록 예약해 두었다. 다시 확인하거나 다른 기기를 추가할 때는:

**고급 설정 → LAN → 고정 IP 리스 목록 → 추가** 에서 MAC 주소와 IP를 넣고 **적용/저장**을 누른다. 적용 시 DHCP 서버가 재시작되며 무선 연결이 몇 초간 끊길 수 있다.

> 파이 안에서 netplan으로 static IP를 박는 방법도 있지만, 공유기 예약이 더 낫다. 주소 충돌을 공유기가 직접 막아주고, OS를 다시 설치해도 설정이 남으며, 파이 설정을 잘못 건드려 접속 불능이 될 위험이 없다.

### 1-2. SSH 접속

```powershell
pc> ssh admin@<PI_IP>
```

비밀번호는 현재 `admin`. 접속되면 다음 단계로.

### 1-3. 비밀번호부터 바꾸기 (건너뛰지 말 것)

곧 Tailscale로 외부에서도 닿게 만들 예정이라, `admin/admin` 상태로 두면 위험하다.

```bash
pi$ passwd
```

### 1-4. 시스템 업데이트

```bash
pi$ sudo apt update && sudo apt full-upgrade -y
pi$ sudo apt install -y ca-certificates curl git ufw unattended-upgrades
pi$ sudo reboot
```

재부팅 후 다시 접속한다. `unattended-upgrades`는 보안 패치를 자동으로 받아준다.

### 1-5. 호스트 이름과 시간대

```bash
pi$ sudo hostnamectl set-hostname webPi
pi$ sudo timedatectl set-timezone Asia/Seoul
```

`/etc/hosts`의 `127.0.1.1` 줄도 `webPi`로 맞춰둔다.

```bash
pi$ sudo sed -i 's/^127\.0\.1\.1.*/127.0.1.1\twebPi/' /etc/hosts
```

### 1-6. SSH 키 인증 (앱이 이 방식으로 접속한다)

GUI 앱은 비밀번호를 저장하지 않고 **키 파일로만** 접속하므로 이 단계가 필수다. 노트북에서:

```powershell
pc> ssh-keygen -t ed25519 -C "webPi"        # 이미 있으면 건너뛴다
pc> type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh admin@<PI_IP> "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

새 터미널에서 비밀번호 없이 들어가지는지 확인한다.

```powershell
pc> ssh admin@<PI_IP>
```

### 1-7. 비밀번호 로그인 끄기

**키 접속이 확인된 뒤에만** 한다.

```bash
pi$ sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
pi$ sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
pi$ sudo systemctl restart ssh
```

기존 세션은 유지되니, 끊지 말고 **새 창에서 접속이 되는지 확인**한 다음 닫는다.

### 1-8. 방화벽

```bash
pi$ sudo ufw allow OpenSSH
pi$ sudo ufw --force enable
pi$ sudo ufw status
```

웹 포트(80/443)는 열지 않는다. 외부 공개는 Cloudflare Tunnel이 바깥으로 나가는 연결로 처리하므로 인바운드를 열 이유가 없다.

---

## 2. Tailscale — 집 밖에서도 접속

### 2-1. webPi에 설치

```bash
pi$ curl -fsSL https://tailscale.com/install.sh | sh
pi$ sudo tailscale up
```

출력된 URL을 노트북 브라우저에서 열어 로그인하면 기기가 등록된다.

### 2-2. 주소 확인

```bash
pi$ tailscale ip -4        # 예: 100.101.102.103
pi$ tailscale status
```

Tailscale 관리 콘솔(https://login.tailscale.com/admin/dns)에서 **MagicDNS**를 켜두면 `webpi` 라는 이름만으로 접속할 수 있다.

### 2-3. 노트북에도 설치

Windows용 Tailscale을 설치하고 **같은 계정**으로 로그인한다. 그다음:

```powershell
pc> ssh admin@100.101.102.103      # tailnet IP
pc> ssh admin@webpi                # MagicDNS를 켰다면 이 이름으로도 접속
```

집 밖(휴대폰 핫스팟 등)에서도 같은 명령이 그대로 동작하는지 한 번 확인해두면 좋다.

### 2-4. ufw와 Tailscale

tailnet 안에서만 열어두고 싶은 포트가 생기면 이렇게 한다.

```bash
pi$ sudo ufw allow in on tailscale0
```

> **참고**: `tailscale up --ssh`(Tailscale SSH)는 키 없이 접속할 수 있어 편하지만, 우리 앱은 paramiko로 표준 SSH·키 인증을 쓰므로 **기본 OpenSSH를 그대로 두는 쪽**이 안전하다. Tailscale은 "어디서든 닿는 네트워크"로만 쓰고, 인증은 1-6에서 만든 키로 한다.

---

## 3. Docker 설치

```bash
pi$ curl -fsSL https://get.docker.com | sudo sh
pi$ sudo usermod -aG docker admin
pi$ exit
```

다시 접속한 뒤 그룹이 적용됐는지 확인한다.

```bash
pi$ docker run --rm hello-world
pi$ docker compose version
```

`sudo` 없이 위 두 명령이 되면 성공이다. GUI 앱이 `docker compose` 명령을 sudo 없이 실행하므로 이 설정이 필요하다.

---

## 4. cloudflared 설치와 로그인

GUI 앱의 온보딩은 **내 PC만** 처리한다. 라즈베리파이에는 직접 깔아야 한다.

```bash
pi$ sudo mkdir -p --mode=0755 /usr/share/keyrings
pi$ curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
pi$ echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
pi$ sudo apt update && sudo apt install -y cloudflared
pi$ cloudflared --version
```

로그인한다. 화면에 URL이 뜨면 노트북 브라우저에서 열고 도메인을 선택한다.

```bash
pi$ cloudflared tunnel login
```

`~/.cloudflared/cert.pem` 이 생기면 준비 끝이다. 이 인증서가 있어야 앱에서 원격 터널을 만들 수 있다.

---

## 5. 프로젝트 배치 규칙

여러 프로젝트를 얹을 거라 규칙을 먼저 정한다.

### 5-1. 폴더 구조

```
/srv/apps/
├── blog/          ← GitHub 저장소 클론
│   ├── docker-compose.yml
│   └── .env
├── api/
└── shop/
```

```bash
pi$ sudo mkdir -p /srv/apps
pi$ sudo chown admin:admin /srv/apps
```

### 5-2. 포트 규칙

각 프로젝트는 **127.0.0.1에만** 바인딩한다. 그래야 tailnet·터널을 통해서만 접근되고 LAN에 노출되지 않는다.

| 프로젝트 | 로컬 포트 | 접속 주소 |
|---|---|---|
| blog | 8001 | blog.내도메인 |
| api | 8002 | api.내도메인 |
| shop | 8003 | shop.내도메인 |

`docker-compose.yml` 예시:

```yaml
services:
  web:
    build: .
    restart: unless-stopped          # 재부팅해도 자동 기동
    ports:
      - "127.0.0.1:8001:8000"        # 왼쪽이 Pi의 포트, 오른쪽이 컨테이너 포트
    env_file: .env
    depends_on: [db]
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    # ports 를 쓰지 않는다 → 컨테이너 네트워크 안에서만 접근
volumes:
  pgdata:
```

### 5-3. GitHub에서 클론

**공개 저장소**면 그냥 클론한다.

```bash
pi$ cd /srv/apps && git clone https://github.com/<계정>/<저장소>.git blog
```

**비공개 저장소**면 프로젝트마다 배포 키(Deploy Key)를 만든다. 읽기 전용이라 안전하다.

```bash
pi$ ssh-keygen -t ed25519 -f ~/.ssh/deploy_blog -N "" -C "webPi blog"
pi$ cat ~/.ssh/deploy_blog.pub
```

출력된 공개키를 GitHub 저장소 → Settings → Deploy keys → Add deploy key 에 붙여넣는다(쓰기 권한 체크 안 함). 그다음 `~/.ssh/config`에 항목을 추가한다.

```bash
pi$ cat >> ~/.ssh/config <<'EOF'

Host github-blog
    HostName github.com
    User git
    IdentityFile ~/.ssh/deploy_blog
    IdentitiesOnly yes
EOF
pi$ chmod 600 ~/.ssh/config
pi$ ssh -T github-blog          # 처음 접속 시 known_hosts 등록 (yes)
pi$ cd /srv/apps && git clone github-blog:<계정>/<저장소>.git blog
```

`.env` 같은 비밀 파일은 저장소에 넣지 말고 서버에서 직접 만든다.

```bash
pi$ nano /srv/apps/blog/.env
```

### 5-4. 수동으로 한 번 띄워보기

```bash
pi$ cd /srv/apps/blog && docker compose up --build -d
pi$ curl -I http://127.0.0.1:8001
pi$ docker compose logs -f
```

여기까지 되면 앱에 등록할 준비가 끝났다.

---

## 6. GUI 앱에 등록

### 6-1. SSH 대상 추가

앱 상단 **대상** 드롭다운 → `SSH 대상 추가...`

| 항목 | 값 |
|---|---|
| 이름 | webPi |
| 호스트 | `100.101.102.103` 또는 `webpi` (MagicDNS) |
| 포트 | 22 |
| 사용자 | admin |
| 키 파일 | `C:\Users\<사용자>\.ssh\id_ed25519` |

**연결 테스트**를 눌러 성공하는지 확인하고 저장한다.

### 6-2. 대상 전환

드롭다운에서 **webPi**를 고른다. 이제 화면의 모든 조작이 라즈베리파이를 향한다. 터널 목록은 Cloudflare 계정 단위라 로컬과 같아 보이지만, 실행 여부·설정 파일·서버 명령은 각 머신의 것이다.

### 6-3. 터널과 라우트 만들기

**터널 생성** → 이름(예: `webpi`) → 서브도메인·도메인 → 로컬 서비스 `http://localhost:8001`

두 번째 프로젝트부터는 같은 터널에 **라우트 추가**로 붙이면 된다.

| 이름 | 도메인 | 로컬 서비스 |
|---|---|---|
| blog | blog.내도메인 | http://localhost:8001 |
| api | api.내도메인 | http://localhost:8002 |

### 6-4. 서버(컨테이너) 등록

라우트의 ⋮ → 편집에서:

| 항목 | 값 |
|---|---|
| 서비스 종류 | 도커 컴포즈 |
| 시작 명령 | `git pull --ff-only && docker compose up --build -d` |
| 정지 명령 | `docker compose down` |
| 작업 폴더 | `/srv/apps/blog` |
| 터널과 함께 시작 | 취향껏 |

**시작 명령에 `git pull`을 넣은 것이 핵심이다.** 서버 토글을 껐다 켜는 것만으로 최신 코드를 받아 다시 빌드하는, Railway의 "Redeploy" 버튼과 같은 동작이 된다.

### 6-5. 켜기

터널 토글 → 초록불 확인 → 서버 토글 → 스피너가 멈추면 브라우저에서 접속 확인.

빌드는 몇 분 걸릴 수 있다. 진행 상황은 **로그** 버튼의 해당 탭에서 실시간으로 볼 수 있다.

---

## 7. GitHub 업데이트 반영하기

세 가지 방법이 있다. 아래로 갈수록 자동이지만 손이 더 간다.

### 방법 A. 수동 (지금 바로 가능)

앱에서 해당 서버 토글을 껐다 켠다. 6-4의 시작 명령이 `git pull` 후 재빌드하므로 그것으로 끝이다.

### 방법 B. 주기적 자동 배포 (권장)

5분마다 원격 저장소를 확인해 변경이 있을 때만 다시 띄운다. 인바운드 노출이 없어 안전하고 설정이 단순하다.

배포 스크립트를 만든다.

```bash
pi$ sudo tee /usr/local/bin/deploy-app <<'EOF'
#!/usr/bin/env bash
# 사용법: deploy-app <프로젝트폴더명>
set -euo pipefail
APP="$1"
DIR="/srv/apps/$APP"
cd "$DIR"

git fetch --quiet origin
LOCAL=$(git rev-parse @)
REMOTE=$(git rev-parse @{u})
if [ "$LOCAL" = "$REMOTE" ]; then
  echo "$(date '+%F %T') [$APP] 변경 없음"
  exit 0
fi

echo "$(date '+%F %T') [$APP] 새 커밋 발견: ${LOCAL:0:7} -> ${REMOTE:0:7}"
git pull --ff-only
docker compose up --build -d
docker image prune -f          # 오래된 이미지 정리 (SD카드 용량 보호)
echo "$(date '+%F %T') [$APP] 배포 완료"
EOF
pi$ sudo chmod +x /usr/local/bin/deploy-app
```

systemd 타이머로 돌린다(프로젝트마다 하나씩).

```bash
pi$ sudo tee /etc/systemd/system/deploy@.service <<'EOF'
[Unit]
Description=Auto deploy %i from GitHub
After=network-online.target docker.service

[Service]
Type=oneshot
User=admin
ExecStart=/usr/local/bin/deploy-app %i
EOF

pi$ sudo tee /etc/systemd/system/deploy@.timer <<'EOF'
[Unit]
Description=Check %i for updates every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

pi$ sudo systemctl daemon-reload
pi$ sudo systemctl enable --now deploy@blog.timer
pi$ systemctl list-timers 'deploy@*'
```

로그는 이렇게 본다.

```bash
pi$ journalctl -u deploy@blog.service -n 50 --no-pager
```

끄고 싶으면 `sudo systemctl disable --now deploy@blog.timer`.

### 방법 C. 푸시 즉시 배포 (웹훅)

GitHub에 푸시하는 순간 반영된다. Cloudflare Tunnel에 배포용 라우트를 하나 더 만들어 쓴다.

```bash
pi$ sudo apt install -y webhook
pi$ mkdir -p ~/webhook
pi$ nano ~/webhook/hooks.json
```

```json
[
  {
    "id": "deploy-blog",
    "execute-command": "/usr/local/bin/deploy-app",
    "pass-arguments-to-command": [
      { "source": "string", "name": "blog" }
    ],
    "trigger-rule": {
      "and": [
        {
          "match": {
            "type": "payload-hmac-sha256",
            "secret": "<긴_랜덤_문자열>",
            "parameter": { "source": "header", "name": "X-Hub-Signature-256" }
          }
        },
        {
          "match": {
            "type": "value",
            "value": "refs/heads/main",
            "parameter": { "source": "payload", "name": "ref" }
          }
        }
      ]
    }
  }
]
```

서비스로 등록한다.

```bash
pi$ sudo tee /etc/systemd/system/webhook.service <<'EOF'
[Unit]
Description=GitHub webhook receiver
After=network-online.target

[Service]
User=admin
ExecStart=/usr/bin/webhook -hooks /home/admin/webhook/hooks.json -ip 127.0.0.1 -port 9000 -verbose
Restart=always

[Install]
WantedBy=multi-user.target
EOF
pi$ sudo systemctl enable --now webhook
```

앱에서 라우트를 하나 더 추가한다: `deploy.내도메인` → `http://localhost:9000`.
GitHub 저장소 → Settings → Webhooks → Add webhook:

- Payload URL: `https://deploy.내도메인/hooks/deploy-blog`
- Content type: `application/json`
- Secret: 위에 넣은 `<긴_랜덤_문자열>`
- 이벤트: Just the push event

> 이 주소는 인터넷에 열린다. **HMAC 시크릿을 반드시 설정**하고, 가능하면 Cloudflare Access로 한 번 더 막는 것을 권한다.

---

## 8. 재부팅해도 살아 있게

정전이나 재부팅 뒤에도 알아서 돌아오게 해두자.

**컨테이너**는 compose에 `restart: unless-stopped`를 넣었으면 Docker가 알아서 다시 띄운다.

**터널**은 앱에서 켠 것이 아니라 systemd 서비스로 만들어두는 편이 확실하다. 앱으로 만든 터널 설정을 그대로 쓴다.

```bash
pi$ ls ~/.cloudflared/            # config-<터널이름>.yml 확인
pi$ sudo tee /etc/systemd/system/cloudflared-webpi.service <<'EOF'
[Unit]
Description=cloudflared tunnel webpi
After=network-online.target
Wants=network-online.target

[Service]
User=admin
ExecStart=/usr/bin/cloudflared --config /home/admin/.cloudflared/config-webpi.yml --no-autoupdate tunnel run webpi
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
pi$ sudo systemctl daemon-reload
pi$ sudo systemctl enable --now cloudflared-webpi
pi$ systemctl status cloudflared-webpi --no-pager
```

> 이렇게 하면 터널의 주인이 systemd가 되므로, **GUI 앱의 터널 토글은 쓰지 않는다**(둘 다 켜면 커넥터가 두 개 붙는다). 앱은 상태 확인과 서버(컨테이너) 조작 용도로 쓰고, 터널 자체는 systemd에 맡기는 구성이다. 라우트를 추가·수정했다면 `sudo systemctl restart cloudflared-webpi` 로 반영한다.

---

## 9. 운영 팁

**디스크 관리** — SD카드/SSD가 이미지로 금방 찬다.

```bash
pi$ docker system df
pi$ docker system prune -af --volumes    # 주의: 안 쓰는 볼륨까지 지운다
pi$ df -h
```

**롤백** — 배포가 잘못됐을 때.

```bash
pi$ cd /srv/apps/blog
pi$ git log --oneline -5
pi$ git checkout <이전커밋>
pi$ docker compose up --build -d
```

**로그 보기** — 앱의 로그 버튼으로도 되고, 직접 볼 수도 있다.

```bash
pi$ docker compose -f /srv/apps/blog/docker-compose.yml logs -f --tail 100
```

**메모리** — Pi 5에서 Postgres 여러 개는 부담이다. 가벼운 서비스는 SQLite를 고려하고, `docker stats`로 사용량을 지켜보자.

**이미지 아키텍처** — 반드시 **arm64**여야 한다. 노트북(x86)에서 빌드해 올리면 `exec format error`가 난다. Pi에서 직접 빌드하거나 `docker buildx build --platform linux/arm64`를 쓴다.

---

## 10. 안 될 때 확인 순서

| 증상 | 확인할 것 |
|---|---|
| SSH 접속 안 됨 (LAN) | 공유기에서 IP 재확인, `ping <PI_IP>` |
| SSH 접속 안 됨 (외부) | 노트북·Pi 양쪽 `tailscale status`, 둘 다 같은 계정인지 |
| 앱 연결 테스트 실패 | 키 파일 경로, 사용자명 `admin`, 1-7 이후 키 접속이 되는지 |
| 터널은 초록인데 502 | 컨테이너가 떠 있는지(`docker compose ps`), 포트가 라우트의 로컬 서비스와 같은지 |
| 사이트가 안 열림 | Cloudflare DNS에 CNAME이 있는지, 터널이 실행 중인지, config의 ingress에 그 hostname이 있는지 |
| 자동 배포가 안 됨 | `journalctl -u deploy@blog.service`, 배포 키 권한, 브랜치 이름(main/master) |
| 빌드가 실패 | 앱 로그 탭 또는 `docker compose build` 직접 실행해 에러 확인 |
| 컨테이너가 계속 재시작 | `docker compose logs`, `.env` 값 누락, arm64 이미지인지 |

---

## 부록: 프로젝트 추가 체크리스트

새 프로젝트를 얹을 때 반복하는 순서다.

1. 포트 정하기 (5-2 표에 한 줄 추가)
2. `/srv/apps/<이름>` 으로 클론 (비공개면 배포 키 발급)
3. `.env` 작성, `docker-compose.yml`의 포트를 `127.0.0.1:<포트>:<컨테이너포트>` 로
4. `docker compose up --build -d` 로 수동 확인
5. 앱에서 라우트 추가 → 서비스 등록(시작 명령에 `git pull --ff-only &&` 포함)
6. 자동 배포가 필요하면 `sudo systemctl enable --now deploy@<이름>.timer`
7. 터널을 systemd로 돌리고 있다면 `sudo systemctl restart cloudflared-webpi`
