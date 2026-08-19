"""서버 주소(http://localhost:8000)를 '다른 기기에서도 열리는 주소'로 바꾸기.

서버 카테고리에는 도메인이 없어서, 그 자리에 장치 IP 기반 링크를 보여준다.
"""
from app.core.service_url import device_url


def test_replaces_localhost_with_device_host():
    assert device_url("http://localhost:8000", "192.168.0.10") == "http://192.168.0.10:8000"


def test_replaces_loopback_ip():
    assert device_url("http://127.0.0.1:3000", "10.0.0.5") == "http://10.0.0.5:3000"


def test_replaces_wildcard_bind_address():
    """0.0.0.0은 '모든 인터페이스'라 그대로는 열리지 않는다."""
    assert device_url("http://0.0.0.0:8080", "10.0.0.5") == "http://10.0.0.5:8080"


def test_keeps_path_and_scheme():
    assert device_url("https://localhost:8443/admin", "pi.local") == "https://pi.local:8443/admin"


def test_keeps_default_port_omitted():
    assert device_url("http://localhost", "10.0.0.5") == "http://10.0.0.5"


def test_hostname_that_is_already_remote_is_left_alone():
    """이미 다른 기기를 가리키는 주소는 건드리지 않는다."""
    assert device_url("http://192.168.0.99:8000", "10.0.0.5") == ""


def test_without_a_host_there_is_no_link():
    assert device_url("http://localhost:8000", "") == ""


def test_non_http_service_has_no_link():
    """터널 ingress에는 tcp://, unix: 같은 것도 올 수 있다."""
    assert device_url("tcp://localhost:22", "10.0.0.5") == ""
    assert device_url("", "10.0.0.5") == ""


def test_host_equal_to_localhost_gives_no_link():
    """대상이 내 PC인데 IP를 못 찾은 경우 - localhost 링크를 또 보여줄 이유가 없다."""
    assert device_url("http://localhost:8000", "localhost") == ""
