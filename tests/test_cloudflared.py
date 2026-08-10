import pytest
from app.core.cloudflared import CloudflaredClient, CloudflaredError
from app.core.runner import CommandRunner, RunResult


class FakeRunner(CommandRunner):
    name = "fake"

    def __init__(self, responses: dict[str, RunResult]):
        self.responses = responses  # key: 첫 매칭 서브명령 문자열
        self.calls: list[list[str]] = []

    def run(self, cmd, timeout=60.0):
        self.calls.append(cmd)
        joined = " ".join(cmd)
        for key, res in self.responses.items():
            if key in joined:
                return res
        return RunResult(0, "", "")

    def spawn(self, cmd, cwd=None, on_line=None, on_exit=None):
        raise NotImplementedError

    def read_file(self, path): return ""
    def write_file(self, path, text): pass
    def file_exists(self, path): return False
    def remove_file(self, path): pass
    def home_dir(self): return "C:/Users/me"


LIST_JSON = ('[{"id":"aaa-111","name":"mysite","created_at":"2026-08-01T00:00:00Z",'
             '"connections":[{"id":"c1"},{"id":"c2"}]},'
             '{"id":"bbb-222","name":"api","created_at":"2026-08-02T00:00:00Z",'
             '"connections":[]}]')


def test_list_tunnels_parses_json():
    r = FakeRunner({"tunnel list": RunResult(0, LIST_JSON, "")})
    tunnels = CloudflaredClient(r).list_tunnels()
    assert [t.name for t in tunnels] == ["mysite", "api"]
    assert tunnels[0].connections == 2
    assert tunnels[1].connections == 0


def test_create_tunnel_parses_id_and_credentials():
    out = ("Tunnel credentials written to "
           "C:/Users/me/.cloudflared/a1b2c3d4-1111-2222-3333-444455556666.json.\n"
           "Created tunnel mysite with id a1b2c3d4-1111-2222-3333-444455556666")
    r = FakeRunner({"tunnel create": RunResult(0, out, "")})
    tid, cred = CloudflaredClient(r).create_tunnel("mysite")
    assert tid == "a1b2c3d4-1111-2222-3333-444455556666"
    assert cred.endswith(f"{tid}.json")


def test_error_raises_with_stderr():
    r = FakeRunner({"tunnel create": RunResult(1, "", "tunnel with name already exists")})
    with pytest.raises(CloudflaredError, match="already exists"):
        CloudflaredClient(r).create_tunnel("mysite")


def test_version_none_when_missing():
    class Boom(FakeRunner):
        def run(self, cmd, timeout=60.0):
            raise FileNotFoundError

    assert CloudflaredClient(Boom({})).version() is None


def test_paths_and_run_args():
    c = CloudflaredClient(FakeRunner({}))
    assert c.config_dir() == "C:/Users/me/.cloudflared"
    assert c.config_path("mysite") == "C:/Users/me/.cloudflared/config-mysite.yml"
    assert c.run_args("mysite") == [
        "cloudflared", "--config", "C:/Users/me/.cloudflared/config-mysite.yml",
        "tunnel", "run", "mysite"]


def test_route_dns_builds_command():
    r = FakeRunner({})
    CloudflaredClient(r).route_dns("mysite", "mysite.example.com")
    assert r.calls[-1] == ["cloudflared", "tunnel", "route", "dns",
                          "mysite", "mysite.example.com"]


def test_list_tunnels_invalid_json_raises_cloudflared_error():
    r = FakeRunner({"tunnel list": RunResult(0, "not-json!!", "")})
    with pytest.raises(CloudflaredError, match="JSON 파싱 실패"):
        CloudflaredClient(r).list_tunnels()
