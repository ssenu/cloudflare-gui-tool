from app.core.config_yml import parse_config
from app.core.wizard_logic import (execute_creation, plan_steps, validate_name,
                                   validate_service, validate_subdomain)


class FakeClient:
    """execute_creation이 새 build_config(tid, cred, routes) 시그니처를 쓰는지 확인용 fake."""

    def __init__(self):
        self.written: dict[str, str] = {}
        self.routed = []

        class _Runner:
            def __init__(self, outer):
                self.outer = outer

            def write_file(self, path, text):
                self.outer.written[path] = text

        self.runner = _Runner(self)

    def create_tunnel(self, name):
        return "tunnel-id-1234", "/creds/tunnel-id-1234.json"

    def route_dns(self, name, hostname):
        self.routed.append((name, hostname))

    def config_path(self, name):
        return f"/config/config-{name}.yml"


def test_validate_name():
    assert validate_name("mysite", []) == ""
    assert "입력" in validate_name("", [])
    assert "존재" in validate_name("dup", ["dup"])
    assert validate_name("한글불가!", []) != ""


def test_validate_subdomain():
    assert validate_subdomain("mysite") == ""
    assert validate_subdomain("my-site") == ""
    assert validate_subdomain("") != ""
    assert validate_subdomain("bad_sub!") != ""


def test_validate_service():
    assert validate_service("http://localhost:8000") == ""
    assert validate_service("https://127.0.0.1:8443") == ""
    assert validate_service("localhost:8000") != ""  # 스킴 필수
    assert validate_service("") != ""


def test_plan_steps_previews_commands():
    steps = plan_steps("mysite", "mysite.example.com", "http://localhost:8000")
    assert len(steps) == 3
    assert "cloudflared tunnel create mysite" in steps[0].preview
    assert "route dns mysite mysite.example.com" in steps[1].preview
    assert "config-mysite.yml" in steps[2].preview


def test_execute_creation_writes_config_with_new_build_config_signature():
    client = FakeClient()
    events = []

    execute_creation(client, "mysite", "mysite.example.com",
                     "http://localhost:8000", lambda i, m, ok: events.append((i, m, ok)))

    assert client.routed == [("mysite", "mysite.example.com")]
    text = client.written["/config/config-mysite.yml"]
    cfg = parse_config(text)
    assert cfg["tunnel"] == "tunnel-id-1234"
    assert cfg["credentials-file"] == "/creds/tunnel-id-1234.json"
    ingress = cfg["ingress"]
    assert ingress[0] == {"hostname": "mysite.example.com",
                          "service": "http://localhost:8000"}
    assert ingress[-1] == {"service": "http_status:404"}
    assert all(ok for _, _, ok in events)
