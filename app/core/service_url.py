"""서버 주소를 '다른 기기에서도 열리는 주소'로 바꾼다.

서버 카테고리에는 도메인이 없다. 그 자리에 무엇을 보여줄까 - 등록된 주소는
보통 ``http://localhost:8000``인데, 그 localhost는 **서버가 도는 기기**를
가리킨다. 앱을 보고 있는 사람의 PC가 아니다. 그대로 링크로 걸면 원격 대상의
서버를 내 PC에서 찾다가 실패한다.

그래서 localhost 자리에 그 장치의 주소(원격이면 SSH로 접속한 host, 내 PC면
LAN IP)를 끼워 넣은 링크를 만든다.
"""

from __future__ import annotations

import socket
from urllib.parse import urlsplit, urlunsplit

# 이 이름들은 "이 기기 자신"을 뜻한다 - 다른 기기에서 열려면 바꿔야 한다.
# 0.0.0.0/::는 '모든 인터페이스에 바인딩'이라 주소로는 열리지 않는다.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::", "[::]", "::1", "[::1]"}


def device_url(service: str, host: str) -> str:
    """service의 localhost를 host로 바꾼 주소. 만들 수 없으면 빈 문자열.

    빈 문자열을 주는 경우:
    - host를 모를 때 (LAN IP 탐지 실패 등)
    - service가 http/https가 아닐 때 (tcp:// 등은 브라우저로 못 연다)
    - service가 이미 다른 기기를 가리킬 때 (바꿀 이유가 없다)
    - host 자체가 localhost일 때 (같은 주소를 두 번 보여줄 뿐이다)
    """
    host = (host or "").strip()
    if not host or host.lower() in LOCAL_HOSTS:
        return ""
    parts = urlsplit((service or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    if parts.hostname.lower() not in LOCAL_HOSTS:
        return ""
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query,
                       parts.fragment))


def local_lan_ip() -> str:
    """이 PC가 공유기에서 쓰는 IP. 못 찾으면 빈 문자열.

    UDP 소켓을 '연결'만 해서 어느 인터페이스로 나가는지 커널에 물어본다 -
    패킷은 실제로 나가지 않으므로 인터넷이 끊겨 있어도 빠르게 답이 온다.
    socket.gethostbyname(hostname)을 쓰지 않는 이유는 윈도우에서 127.0.0.1이나
    VPN/가상 어댑터 주소를 돌려주는 경우가 흔하기 때문이다.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.2)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()
    return "" if ip.startswith("127.") else ip
