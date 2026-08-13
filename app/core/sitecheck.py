"""공개 주소가 실제로 열리는지 확인하고, 안 되면 원인을 짚어 준다.

터널·서버 토글이 모두 켜져 있어도 사이트가 안 뜨는 경우가 있고, 그때 사람이
하는 일은 늘 같다 - 주소를 브라우저에 넣어보고 상태 코드로 원인을 좁힌다.
그 판단을 앱이 대신한다.

- 404: cloudflared의 ingress에 그 hostname이 없다. 라우트를 추가했는데도
  404면 실행 중인 cloudflared가 옛 설정을 들고 있는 것이다(재시작 필요).
- 502/503: 라우트는 맞는데 그 포트에 서버가 없다.
- 530/1033: 터널 자체가 Cloudflare에 붙어 있지 않다.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass

TIMEOUT = 10.0
USER_AGENT = "cloudflare-tunnel-gui/1.0"


@dataclass
class SiteCheckResult:
    url: str
    status: int          # 0이면 응답 자체를 못 받음
    ok: bool
    headline: str
    hint: str = ""


def diagnose(status: int) -> tuple[bool, str, str]:
    """상태 코드로 (정상 여부, 한 줄 판정, 다음에 할 일)을 돌려준다."""
    if 200 <= status < 400:
        return True, f"정상 ({status})", ""
    if status == 404:
        return False, "404 - 이 주소로 오는 요청을 받을 곳이 없습니다", (
            "터널 설정에 이 도메인이 없습니다. 라우트를 추가했다면 "
            "터널을 껐다 켜야 반영됩니다.")
    if status in (502, 503):
        return False, f"{status} - 터널은 붙었는데 서버가 응답하지 않습니다", (
            "라우트의 '로컬 서비스 주소' 포트에 실제로 서버가 떠 있는지 "
            "확인하세요. 도커라면 compose가 여는 호스트 포트와 같아야 합니다.")
    if status == 530:
        return False, "530 - 터널이 Cloudflare에 연결돼 있지 않습니다", (
            "터널 토글을 켜고, 이 기기에 자격증명이 있는지 확인하세요.")
    if status in (401, 403):
        return False, f"{status} - 접근이 차단되었습니다", (
            "Cloudflare Access 같은 접근 제한이 걸려 있을 수 있습니다.")
    if 500 <= status:
        return False, f"{status} - 서버가 오류를 반환했습니다", (
            "서버 로그를 확인하세요. 터널까지는 정상적으로 전달되고 있습니다.")
    return False, f"{status} - 예상하지 못한 응답입니다", ""


def check_site(hostname: str, opener=None) -> SiteCheckResult:
    """https://<hostname> 을 실제로 요청해 본다.

    opener를 주면 그것으로 연다(테스트용). 리디렉션은 따라가지 않고 그
    응답 코드를 그대로 본다 - 3xx도 "터널까지는 잘 왔다"는 뜻이라 정상이다.
    """
    url = f"https://{hostname}"
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": USER_AGENT})
    try:
        open_fn = opener if opener is not None else urllib.request.urlopen
        with open_fn(req, timeout=TIMEOUT) as res:
            status = getattr(res, "status", 0) or 0
    except urllib.error.HTTPError as ex:
        status = ex.code
    except Exception as ex:
        return SiteCheckResult(
            url=url, status=0, ok=False,
            headline="응답을 받지 못했습니다",
            hint=f"{ex}\n주소가 맞는지, DNS 레코드가 만들어졌는지 확인하세요.")

    ok, headline, hint = diagnose(status)
    return SiteCheckResult(url=url, status=status, ok=ok,
                           headline=headline, hint=hint)
