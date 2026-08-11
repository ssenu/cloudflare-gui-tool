from app.core.run_registry import RunRegistry
from tests.fake_runner import FakeRunner


def make_registry() -> RunRegistry:
    return RunRegistry(FakeRunner())


def test_run_dir_normalizes_backslashes():
    reg = RunRegistry(FakeRunner(home="C:\\Users\\me"))
    assert reg.run_dir() == "C:/Users/me/.cloudflare-gui/run"


def test_unit_naming():
    reg = make_registry()
    assert reg.unit_tunnel("mysite") == "tunnel-mysite"
    assert reg.unit_service("mysite", "abcd1234") == "svc-mysite-abcd1234"


def test_write_read_clear_pid_roundtrip():
    reg = make_registry()
    unit = reg.unit_tunnel("t1")
    assert reg.read_pid(unit) is None  # 아직 없음

    reg.write_pid(unit, 4242)
    assert reg.read_pid(unit) == 4242

    reg.clear_pid(unit)
    assert reg.read_pid(unit) is None


def test_read_pid_invalid_content_returns_none():
    reg = make_registry()
    unit = reg.unit_tunnel("t1")
    reg.runner.write_file(reg.pid_path(unit), "not-a-pid")
    assert reg.read_pid(unit) is None


def test_clear_pid_missing_file_is_noop():
    reg = make_registry()
    reg.clear_pid(reg.unit_tunnel("nope"))  # 예외 없이 조용히 통과


def test_rotate_log_if_big_under_limit_leaves_log_untouched():
    reg = make_registry()
    unit = reg.unit_tunnel("t1")
    reg.runner.write_file(reg.log_path(unit), "small log")
    reg.rotate_log_if_big(unit, limit=5_000_000)
    assert reg.runner.read_file(reg.log_path(unit)) == "small log"
    assert reg.log_path(unit) + ".1" not in reg.runner.files


def test_rotate_log_if_big_moves_content_to_log_1():
    reg = make_registry()
    unit = reg.unit_tunnel("t1")
    big = "x" * 100
    reg.runner.write_file(reg.log_path(unit), big)
    reg.rotate_log_if_big(unit, limit=50)
    assert reg.log_path(unit) not in reg.runner.files
    assert reg.runner.read_file(reg.log_path(unit) + ".1") == big


def test_rotate_log_if_big_missing_log_is_noop():
    reg = make_registry()
    reg.rotate_log_if_big(reg.unit_tunnel("nope"), limit=10)  # 예외 없음


def test_known_units_scans_pid_files():
    reg = make_registry()
    reg.write_pid(reg.unit_tunnel("a"), 1)
    reg.write_pid(reg.unit_service("a", "r1"), 2)
    reg.runner.write_file(reg.log_path(reg.unit_tunnel("a")), "log")  # .pid 아님, 무시돼야
    units = set(reg.known_units())
    assert units == {"tunnel-a", "svc-a-r1"}


def test_known_units_empty_when_no_run_dir():
    reg = make_registry()
    assert reg.known_units() == []


def test_alive_map_single_batched_call():
    reg = make_registry()
    u1, u2, u3 = "tunnel-a", "tunnel-b", "tunnel-c"
    reg.write_pid(u1, 1)
    reg.write_pid(u2, 2)
    reg.runner.live_pids = {1}  # u2의 PID는 죽어있음, u3는 PID 파일 자체가 없음

    result = reg.alive_map([u1, u2, u3])

    assert result == {u1: True, u2: False, u3: False}
    assert reg.runner.pids_alive_calls == 1


# ---- C2: read_pid 왕복 수 감소 ----

class _CountingRunner(FakeRunner):
    """read_file/file_exists 호출 횟수를 세는 FakeRunner. C2 왕복 감소 검증용."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.read_file_calls = 0
        self.file_exists_calls = 0

    def read_file(self, path: str) -> str:
        self.read_file_calls += 1
        return super().read_file(path)

    def file_exists(self, path: str) -> bool:
        self.file_exists_calls += 1
        return super().file_exists(path)


def test_read_pid_is_single_round_trip():
    runner = _CountingRunner()
    reg = RunRegistry(runner)
    unit = reg.unit_tunnel("t1")
    reg.write_pid(unit, 4242)
    runner.read_file_calls = 0
    runner.file_exists_calls = 0

    pid = reg.read_pid(unit)

    assert pid == 4242
    assert runner.read_file_calls == 1
    assert runner.file_exists_calls == 0  # file_exists()를 더 이상 쓰지 않는다


def test_read_pid_missing_file_is_single_round_trip():
    runner = _CountingRunner()
    reg = RunRegistry(runner)

    pid = reg.read_pid(reg.unit_tunnel("nope"))

    assert pid is None
    assert runner.read_file_calls == 1
    assert runner.file_exists_calls == 0


# ---- C3: PID 파일에 cmd 토큰/시작 시각 함께 기록 ----

def test_write_pid_with_cmd_records_token_and_started():
    reg = make_registry()
    unit = reg.unit_tunnel("t1")

    reg.write_pid(unit, 123, cmd="cloudflared")

    content = reg.runner.read_file(reg.pid_path(unit))
    lines = content.splitlines()
    assert lines[0] == "123"
    assert lines[1] == "cmd=cloudflared"
    assert lines[2].startswith("started=")
    assert reg.read_pid(unit) == 123


def test_read_record_returns_pid_and_cmd_token():
    reg = make_registry()
    unit = reg.unit_tunnel("t1")
    reg.write_pid(unit, 123, cmd="cloudflared")

    record = reg.read_record(unit)

    assert record == (123, "cloudflared")


def test_read_record_legacy_plain_pid_has_no_cmd_token():
    reg = make_registry()
    unit = reg.unit_tunnel("t1")
    reg.write_pid(unit, 123)  # cmd 없음: 옛 형식과 동일

    assert reg.read_record(unit) == (123, None)
