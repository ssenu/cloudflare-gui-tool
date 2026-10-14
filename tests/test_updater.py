"""앱 자체 업데이트의 순수 로직: 버전 비교, 릴리스 해석, 설치 종류, 내려받기."""
from __future__ import annotations

import io
import os

import pytest

from app.core import updater
from app.core.updater import (install_kind, is_dev, is_newer, parse_release,
                              parse_version, should_auto_check)

# ---- 버전 ----


@pytest.mark.parametrize("text,expected", [
    ("v0.1.4", (0, 1, 4)),
    ("0.1.4", (0, 1, 4)),
    ("0.2", (0, 2, 0)),
    ("0.0.0-dev", (0, 0, 0)),
    ("1.10.0", (1, 10, 0)),
    ("v2.0.0-rc1", (2, 0, 0)),
    ("", (0, 0, 0)),
])
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_is_newer_compares_numerically_not_as_text():
    """문자열로 비교하면 '0.1.10' < '0.1.9'가 된다."""
    assert is_newer("0.1.10", "0.1.9")
    assert not is_newer("0.1.9", "0.1.10")


def test_same_version_is_not_newer():
    assert not is_newer("v0.1.3", "0.1.3")


def test_dev_build_is_recognised():
    assert is_dev("0.0.0-dev")
    assert not is_dev("0.1.3")


# ---- 릴리스 응답 해석 ----

SAMPLE = {
    "tag_name": "v0.1.4",
    "body": "  - 업데이트 버튼 추가\n",
    "html_url": "https://github.com/ssenu/cloudflare-gui-tool/releases/tag/v0.1.4",
    "assets": [
        {"name": "CloudflareTunnelGUI-0.1.4-portable.zip",
         "browser_download_url": "https://x/portable.zip", "size": 1},
        {"name": "CloudflareTunnelGUI-Setup-0.1.4.exe",
         "browser_download_url": "https://x/setup.exe", "size": 32_000_000},
    ],
}


def test_parse_release_picks_the_setup_asset():
    r = parse_release(SAMPLE)
    assert r.version == "0.1.4"
    assert r.setup_url == "https://x/setup.exe"   # zip이 아니라 설치본
    assert r.setup_size == 32_000_000
    assert r.notes == "- 업데이트 버튼 추가"


def test_parse_release_without_setup_asset():
    """설치본이 아직 안 올라간 릴리스(CI 진행 중)도 죽지 않아야 한다."""
    r = parse_release({"tag_name": "v0.1.4", "assets": []})
    assert r.setup_url == ""
    assert r.page_url.endswith("/releases")


def test_fetch_latest_uses_the_latest_endpoint():
    seen = []
    r = updater.fetch_latest(lambda url, t: seen.append(url) or SAMPLE)
    assert seen == [updater.LATEST_URL]
    assert r.version == "0.1.4"


# ---- 자동 확인 주기 ----

def test_auto_check_once_a_day():
    day = updater.AUTO_CHECK_INTERVAL
    assert should_auto_check(True, last_checked=0, now=1000)
    assert not should_auto_check(True, last_checked=1000, now=1000 + day - 1)
    assert should_auto_check(True, last_checked=1000, now=1000 + day)


def test_auto_check_respects_the_switch():
    assert not should_auto_check(False, last_checked=0, now=10**9)


# ---- 설치 종류 ----

def test_running_from_source_is_source():
    assert install_kind(frozen=False) == "source"


def test_installed_build_has_an_uninstaller_next_to_it(tmp_path):
    exe = tmp_path / "Cloudflare Tunnel GUI.exe"
    exe.write_bytes(b"")
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert install_kind(str(exe), frozen=True) == "installed"


def test_unzipped_build_is_portable(tmp_path):
    exe = tmp_path / "Cloudflare Tunnel GUI.exe"
    exe.write_bytes(b"")
    assert install_kind(str(exe), frozen=True) == "portable"


# ---- 내려받기 ----

class _Resp(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download_reports_progress_and_writes_file(tmp_path):
    data = b"x" * 1000
    seen = []
    dest = str(tmp_path / "setup.exe")

    updater.download("https://x/setup.exe", dest, progress=lambda d, t: seen.append((d, t)),
                     opener=lambda req, timeout: _Resp(data), chunk=300)

    assert open(dest, "rb").read() == data
    assert seen[-1] == (1000, 1000)
    assert not os.path.exists(dest + ".part")


def test_truncated_download_is_not_left_behind(tmp_path):
    """끊긴 파일을 설치본으로 실행하면 안 된다."""
    dest = str(tmp_path / "setup.exe")
    with pytest.raises(IOError):
        updater.download("https://x/setup.exe", dest,
                         opener=lambda req, timeout: _Resp(b"abc", length=999))
    assert not os.path.exists(dest)
    assert not os.path.exists(dest + ".part")
