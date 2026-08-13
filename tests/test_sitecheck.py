"""공개 주소 접속 확인과 상태 코드 진단."""
from __future__ import annotations

import urllib.error

from app.core.sitecheck import check_site, diagnose


class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_returning(status):
    def _open(req, timeout=None):
        return _Resp(status)
    return _open


def opener_raising(exc):
    def _open(req, timeout=None):
        raise exc
    return _open


def test_200_is_ok():
    r = check_site("a.example.com", opener_returning(200))
    assert r.ok and r.status == 200
    assert r.url == "https://a.example.com"
    assert not r.hint


def test_404_points_at_route_or_restart():
    """오늘 실제로 겪은 경우: 라우트는 추가했는데 터널이 옛 설정으로 돌던 상황."""
    r = check_site("a.example.com",
                   opener_raising(urllib.error.HTTPError(
                       "u", 404, "Not Found", {}, None)))
    assert not r.ok and r.status == 404
    assert "터널을 껐다 켜야" in r.hint


def test_502_points_at_local_server_port():
    r = check_site("a.example.com",
                   opener_raising(urllib.error.HTTPError(
                       "u", 502, "Bad Gateway", {}, None)))
    assert not r.ok and r.status == 502
    assert "포트" in r.hint


def test_530_points_at_tunnel_not_connected():
    ok, headline, hint = diagnose(530)
    assert not ok
    assert "연결" in headline
    assert "토글" in hint


def test_redirect_counts_as_reachable():
    ok, _headline, _hint = diagnose(301)
    assert ok  # 터널까지는 정상적으로 도달했다는 뜻


def test_network_failure_is_reported_without_status():
    r = check_site("a.example.com", opener_raising(OSError("이름을 확인할 수 없음")))
    assert r.status == 0 and not r.ok
    assert "응답을 받지 못했습니다" in r.headline
    assert "DNS" in r.hint
