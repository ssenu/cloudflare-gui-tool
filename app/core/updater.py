"""앱 자체 업데이트: GitHub 릴리스에서 새 버전을 찾고, 설치본을 받아 실행한다.

여기에는 화면이 없다. 버전 비교, 릴리스 조회, 설치 종류 판별, 내려받기만 둔다.
네트워크와 파일 시스템은 인자로 바꿔 끼울 수 있어 테스트에서 실제로 GitHub에
붙지 않는다.

왜 설치본을 그냥 실행하면 되나: 설치본은 관리자 권한 없이 사용자 폴더에
깔리고(PrivilegesRequired=lowest), 같은 AppId로 덮어쓰면 업그레이드가 된다.
설정은 %APPDATA%에 따로 있어 그대로 남는다. 앱이 직접 파일을 바꿔치기할 필요가
없다.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

REPO = "ssenu/cloudflare-gui-tool"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
SETUP_RE = re.compile(r"^CloudflareTunnelGUI-Setup-.+\.exe$")

# 자동 확인 주기. 켤 때마다 GitHub에 묻는 건 과하다.
AUTO_CHECK_INTERVAL = 24 * 60 * 60

# 설치본을 조용히 돌리는 인자. /CLOSEAPPLICATIONS는 혹시 아직 떠 있는 우리
# 프로세스를 정리하고, 설치가 끝나면 스크립트의 [Run]이 앱을 다시 띄운다.
SETUP_ARGS = ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]


@dataclass
class Release:
    version: str        # "0.1.4" (앞의 v는 뗀다)
    notes: str          # 릴리스 본문(변경 내용)
    page_url: str       # 사람이 볼 릴리스 페이지
    setup_url: str      # 설치본 직접 다운로드 주소. 없으면 ""
    setup_size: int     # 바이트. 모르면 0


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.1.4', '0.1.4' -> (0, 1, 4). 개발판('0.0.0-dev')은 (0, 0, 0).

    숫자가 아닌 꼬리(-dev, -rc1)는 버린다. 비교는 숫자 부분만으로 한다.
    """
    core = (text or "").strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for p in core.split("."):
        m = re.match(r"\d+", p)
        parts.append(int(m.group()) if m else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_dev(version: str) -> bool:
    return parse_version(version) == (0, 0, 0)


def is_newer(latest: str, current: str) -> bool:
    """latest가 current보다 새 버전인가."""
    return parse_version(latest) > parse_version(current)


def parse_release(data: dict) -> Release:
    """GitHub API의 releases/latest 응답 -> Release."""
    setup_url, setup_size = "", 0
    for asset in data.get("assets") or []:
        if SETUP_RE.match(asset.get("name", "")):
            setup_url = asset.get("browser_download_url", "")
            setup_size = int(asset.get("size") or 0)
            break
    return Release(
        version=(data.get("tag_name") or "").lstrip("vV"),
        notes=(data.get("body") or "").strip(),
        page_url=data.get("html_url") or f"https://github.com/{REPO}/releases",
        setup_url=setup_url,
        setup_size=setup_size,
    )


def _http_get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "CloudflareTunnelGUI-updater",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_latest(get_json: Callable[[str, float], dict] = _http_get_json,
                 timeout: float = 10.0) -> Release:
    """최신 릴리스를 묻는다. 네트워크 오류는 그대로 올린다(호출측이 표시)."""
    return parse_release(get_json(LATEST_URL, timeout))


def should_auto_check(enabled: bool, last_checked: float,
                      now: float | None = None) -> bool:
    """켤 때 자동 확인을 할 차례인가. 하루에 한 번만."""
    if not enabled:
        return False
    if not last_checked:
        return True     # 한 번도 확인한 적 없음
    now = time.time() if now is None else now
    return now - last_checked >= AUTO_CHECK_INTERVAL


def install_kind(executable: str | None = None, frozen: bool | None = None) -> str:
    """지금 앱이 어떻게 깔려 있나.

    - "installed": 설치본으로 깔림 - 옆에 제거 프로그램(unins000.exe)이 있다.
      이때만 앱이 설치본을 받아 스스로 업데이트한다.
    - "portable": 빌드된 exe인데 제거 프로그램이 없음(zip을 풀어 씀).
      덮어쓸 위치를 앱이 정할 수 없으니 릴리스 페이지만 안내한다.
    - "source": 파이썬으로 직접 실행 중(개발). 업데이트하지 않는다.
    """
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        return "source"
    exe = executable or sys.executable
    folder = os.path.dirname(os.path.abspath(exe))
    try:
        names = os.listdir(folder)
    except OSError:
        return "portable"
    if any(re.match(r"^unins\d+\.exe$", n, re.I) for n in names):
        return "installed"
    return "portable"


def download(url: str, dest: str,
             progress: Callable[[int, int], None] | None = None,
             opener: Callable = urllib.request.urlopen,
             chunk: int = 256 * 1024, timeout: float = 30.0) -> str:
    """url을 dest로 받는다. progress(받은 바이트, 전체 바이트)를 부른다.

    받는 도중에는 dest + ".part"에 쓰고, 다 받은 뒤에만 이름을 바꾼다 -
    중간에 끊긴 파일을 설치본으로 실행하는 일이 없게 한다.
    """
    part = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "CloudflareTunnelGUI-updater"})
    with opener(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(part, "wb") as f:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                if progress is not None:
                    progress(done, total)
    if total and done != total:
        os.remove(part)
        raise IOError(f"내려받기가 중간에 끊겼습니다 ({done}/{total} 바이트)")
    if os.path.exists(dest):
        os.remove(dest)
    os.replace(part, dest)
    return dest
