from app.core.runner import RunResult
from app.core.config_yml import parse_config
from app.core.wizard_logic import (execute_creation, finish_creation, plan_steps,
                                   validate_name, validate_service, validate_subdomain)


class FakeClient:
    """execute_creation이 새 build_config(tid, cred, routes) 시그니처를 쓰는지 확인용 fake."""

    def __init__(self):
        self.written: dict[str, str] = {}
        self.routed = []
        self.create_calls = 0

        class _Runner:
            def __init__(self, outer):
                self.outer = outer

            def write_file(self, path, text):
                self.outer.written[path] = text

        self.runner = _Runner(self)

    def create_tunnel(self, name):
        self.create_calls += 1
        return "tunnel-id-1234", "/creds/tunnel-id-1234.json"

    def route_dns(self, name, hostname, overwrite=False):
        self.routed.append((name, hostname, overwrite))

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

    assert client.routed == [("mysite", "mysite.example.com", False)]
    text = client.written["/config/config-mysite.yml"]
    cfg = parse_config(text)
    assert cfg["tunnel"] == "tunnel-id-1234"
    assert cfg["credentials-file"] == "/creds/tunnel-id-1234.json"
    ingress = cfg["ingress"]
    assert ingress[0] == {"hostname": "mysite.example.com",
                          "service": "http://localhost:8000"}
    assert ingress[-1] == {"service": "http_status:404"}
    assert all(ok for _, _, ok in events)


def test_execute_creation_fills_created_dict():
    client = FakeClient()
    created: dict = {}

    execute_creation(client, "mysite", "mysite.example.com",
                     "http://localhost:8000", lambda i, m, ok: None, created=created)

    assert created == {"tunnel_id": "tunnel-id-1234",
                       "credentials": "/creds/tunnel-id-1234.json"}


def test_finish_creation_does_not_recreate_tunnel():
    # 재개 경로: DNS 레코드 충돌로 실패한 뒤 터널을 다시 만들지 않고
    # DNS 연결 + 설정 파일 작성만 수행해야 한다.
    client = FakeClient()
    events = []

    finish_creation(client, "mysite", "mysite.example.com", "http://localhost:8000",
                    "tunnel-id-1234", "/creds/tunnel-id-1234.json",
                    lambda i, m, ok: events.append((i, m, ok)), overwrite_dns=True)

    assert client.create_calls == 0
    assert client.routed == [("mysite", "mysite.example.com", True)]
    text = client.written["/config/config-mysite.yml"]
    cfg = parse_config(text)
    assert cfg["tunnel"] == "tunnel-id-1234"
    assert all(ok for _, _, ok in events)


# ---- 터널만 만들기 ----

def test_plan_steps_tunnel_only_has_two_steps():
    from app.core.wizard_logic import plan_steps_tunnel_only
    steps = plan_steps_tunnel_only("mysite")
    assert len(steps) == 2
    assert "tunnel create mysite" in steps[0].preview
    assert "라우트 없음" in steps[1].preview


def test_create_tunnel_only_writes_config_without_routes():
    from app.core.cloudflared import CloudflaredClient
    from app.core.config_yml import get_routes, parse_config
    from app.core.wizard_logic import create_tunnel_only
    from tests.fake_runner import FakeRunner

    runner = FakeRunner(home="/home/fake")
    runner.run_results[("cloudflared", "tunnel", "create", "mysite")] = RunResult(
        0, "Created tunnel mysite with id 11111111-2222-3333-4444-555555555555", "")
    client = CloudflaredClient(runner)
    events = []
    created = {}

    create_tunnel_only(client, "mysite",
                       lambda i, m, ok: events.append((i, ok)), created)

    text = runner.files["/home/fake/.cloudflared/config-mysite.yml"]
    cfg = parse_config(text)
    assert get_routes(cfg) == []            # 라우트 없음
    assert cfg["ingress"] == [{"service": "http_status:404"}]  # 폴백만 남는다
    assert created["tunnel_id"] == "11111111-2222-3333-4444-555555555555"
    # DNS 연결 단계가 없으므로 route dns 명령은 실행되지 않아야 한다
    assert not any("route" in c[0] for c in runner.run_calls)
