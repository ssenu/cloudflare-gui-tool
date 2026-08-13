import subprocess

import pytest
from app.core.cloudflared import CloudflaredClient, CloudflaredError, DnsRecordExistsError
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
    def append_file(self, path, text): pass
    def file_exists(self, path): return False
    def remove_file(self, path): pass
    def remove_tree(self, path): pass
    def home_dir(self): return "C:/Users/me"
    def spawn_detached(self, cmd, cwd, log_path): raise NotImplementedError
    def pids_alive(self, pids, timeout=60.0): return set()
    def pid_cmdlines(self, pids): return {}
    def kill_pid(self, pid): pass
    def tail_file(self, path, offset): return (0, "")
    def file_size(self, path): return 0
    def ensure_dir(self, path): pass
    def list_dir(self, path): return []


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


def test_version_none_on_timeout():
    class TimeoutRunner(FakeRunner):
        def run(self, cmd, timeout=60.0):
            raise subprocess.TimeoutExpired("x", 1)

    assert CloudflaredClient(TimeoutRunner({})).version() is None


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


def test_list_tunnels_null_output_returns_empty():
    # 터널이 0개일 때 cloudflared는 "null"을 출력한다
    r = FakeRunner({"tunnel list": RunResult(0, "null\n", "")})
    assert CloudflaredClient(r).list_tunnels() == []


def test_route_dns_overwrite_adds_flag():
    r = FakeRunner({})
    CloudflaredClient(r).route_dns("mysite", "mysite.example.com", overwrite=True)
    assert r.calls[-1] == ["cloudflared", "tunnel", "route", "dns", "--overwrite-dns",
                          "mysite", "mysite.example.com"]


def test_route_dns_1003_raises_dns_record_exists_error():
    r = FakeRunner({"tunnel route dns": RunResult(
        1, "", "code: 1003, reason: Failed to create record test.example.com with err\n"
                "An A, AAAA, or CNAME record with that host already exists.")})
    with pytest.raises(DnsRecordExistsError):
        CloudflaredClient(r).route_dns("mysite", "test.example.com")


def test_route_dns_already_exists_case_insensitive_raises_dns_record_exists_error():
    r = FakeRunner({"tunnel route dns": RunResult(1, "", "record ALREADY EXISTS here")})
    with pytest.raises(DnsRecordExistsError):
        CloudflaredClient(r).route_dns("mysite", "test.example.com")


def test_route_dns_other_failure_raises_plain_cloudflared_error():
    r = FakeRunner({"tunnel route dns": RunResult(1, "", "some other failure")})
    with pytest.raises(CloudflaredError) as exc_info:
        CloudflaredClient(r).route_dns("mysite", "test.example.com")
    assert not isinstance(exc_info.value, DnsRecordExistsError)


def test_credentials_path_builds_from_config_dir():
    c = CloudflaredClient(FakeRunner({}))
    assert c.credentials_path("a1b2c3d4-1111-2222-3333-444455556666") == (
        "C:/Users/me/.cloudflared/a1b2c3d4-1111-2222-3333-444455556666.json")


def test_has_credentials_true_when_file_exists():
    class ExistsRunner(FakeRunner):
        def file_exists(self, path):
            return path == "C:/Users/me/.cloudflared/tid.json"

    c = CloudflaredClient(ExistsRunner({}))
    assert c.has_credentials("tid") is True


def test_has_credentials_false_when_file_missing():
    c = CloudflaredClient(FakeRunner({}))  # file_exists 항상 False
    assert c.has_credentials("tid") is False


def test_route_dns_unrelated_1003_is_not_dns_exists_error():
    # 포트 번호 등 무관한 숫자에 1003이 들어 있어도 덮어쓰기 제안을 하면 안 된다
    r = FakeRunner({"route dns": RunResult(1, "", "dial tcp 127.0.0.1:10030: refused")})
    with pytest.raises(CloudflaredError) as ei:
        CloudflaredClient(r).route_dns("t", "a.example.com")
    assert not isinstance(ei.value, DnsRecordExistsError)
