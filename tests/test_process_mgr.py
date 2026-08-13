from app.core.cloudflared import CloudflaredClient
from app.core.process_mgr import RUNNING_MARKER, ProcessManager, TunnelState, _cmd_tokens_match
from app.core.run_registry import RunRegistry
from app.core.runner import RunResult
from app.core.store import RouteMeta, ServiceSpec, TunnelMeta
from tests.fake_runner import FakeRunner


def make_mgr(runner: FakeRunner | None = None):
    runner = runner or FakeRunner()
    reg = RunRegistry(runner)
    mgr = ProcessManager(lambda: reg)
    return mgr, runner, reg


def meta(name: str, routes=None) -> TunnelMeta:
    return TunnelMeta(name=name, routes=routes or [])


def command_route(route_id="r1", **spec_kwargs) -> RouteMeta:
    return RouteMeta(id=route_id, hostname="h", service="s",
                     server=ServiceSpec(kind="command", **spec_kwargs))


def docker_route(route_id="r1", cwd="/srv/app") -> RouteMeta:
    return RouteMeta(id=route_id, hostname="h", service="s",
                     server=ServiceSpec(kind="docker", cwd=cwd,
                                        start_cmd="docker compose up -d",
                                        stop_cmd="docker compose down"))


# ---- 터널 상태 판정 표: 4분기 ----

def test_tunnel_state_no_pid_file_is_stopped():
    mgr, runner, reg = make_mgr()
    mgr.refresh([meta("t1")])
    assert mgr.tunnel_state("t1") == TunnelState.STOPPED


def test_tunnel_state_alive_with_marker_is_running():
    mgr, runner, reg = make_mgr()
    unit = reg.unit_tunnel("t1")
    reg.write_pid(unit, 111)
    runner.live_pids.add(111)
    runner.write_file(reg.log_path(unit), "INF Registered tunnel connection ok\n")

    mgr.refresh([meta("t1")])

    assert mgr.tunnel_state("t1") == TunnelState.RUNNING


def test_tunnel_state_alive_without_marker_is_starting():
    mgr, runner, reg = make_mgr()
    unit = reg.unit_tunnel("t1")
    reg.write_pid(unit, 111)
    runner.live_pids.add(111)
    runner.write_file(reg.log_path(unit), "connecting...\n")

    mgr.refresh([meta("t1")])

    assert mgr.tunnel_state("t1") == TunnelState.STARTING


def test_tunnel_state_dead_pid_is_error():
    mgr, runner, reg = make_mgr()
    unit = reg.unit_tunnel("t1")
    reg.write_pid(unit, 111)  # live_pids에는 없음 -> 죽은 것으로 취급

    mgr.refresh([meta("t1")])

    assert mgr.tunnel_state("t1") == TunnelState.ERROR


# ---- 정지 후 상태 ----

def test_stop_tunnel_clears_pid_and_state_is_stopped_not_error():
    mgr, runner, reg = make_mgr()
    unit = reg.unit_tunnel("t1")
    reg.write_pid(unit, 111)
    runner.live_pids.add(111)
    runner.write_file(reg.log_path(unit), f"{RUNNING_MARKER}\n")
    mgr.refresh([meta("t1")])
    assert mgr.tunnel_state("t1") == TunnelState.RUNNING

    mgr.stop_tunnel("t1")

    assert reg.read_pid(unit) is None
    assert mgr.tunnel_state("t1") == TunnelState.STOPPED  # ERROR가 아니어야 함
    assert 111 not in runner.live_pids


def test_start_tunnel_writes_pid_and_spawns():
    mgr, runner, reg = make_mgr()
    client = CloudflaredClient(runner)

    mgr.start_tunnel("t1", client)

    unit = reg.unit_tunnel("t1")
    pid = reg.read_pid(unit)
    assert pid is not None
    assert pid in runner.live_pids
    assert len(runner.spawn_detached_calls) == 1
    assert mgr.tunnel_state("t1") == TunnelState.STARTING  # 마커 전


def test_start_tunnel_offset_ignores_stale_marker_from_previous_run():
    mgr, runner, reg = make_mgr()
    unit = reg.unit_tunnel("t1")
    log_path = reg.log_path(unit)
    # 이전 실행이 남긴 로그(마커 포함)가 이미 존재
    runner.write_file(log_path, f"{RUNNING_MARKER}\n")
    client = CloudflaredClient(runner)

    mgr.start_tunnel("t1", client)
    mgr.refresh([meta("t1")])

    # 새 실행은 아직 마커를 찍지 않았으므로 STARTING이어야 한다 (RUNNING 아님)
    assert mgr.tunnel_state("t1") == TunnelState.STARTING


# ---- 명령 서비스 ----

def test_service_running_reflects_pid_liveness():
    mgr, runner, reg = make_mgr()
    route = command_route(start_cmd="myserver")
    mgr.start_service("t1", route)
    assert mgr.service_running("t1", route) is True

    mgr.stop_service("t1", route)
    assert mgr.service_running("t1", route) is False


def test_stop_service_uses_stop_cmd_when_present():
    mgr, runner, reg = make_mgr()
    route = command_route(start_cmd="myserver", stop_cmd="myserver --stop")
    mgr.start_service("t1", route)
    unit = reg.unit_service("t1", route.id)
    pid = reg.read_pid(unit)

    mgr.stop_service("t1", route)

    assert (("myserver", "--stop"), None) in runner.run_calls
    assert pid in runner.live_pids  # stop_cmd 경로는 kill_pid를 쓰지 않는다
    assert reg.read_pid(unit) is None
    assert mgr.service_running("t1", route) is False


# ---- 도커 서비스 ----

def test_docker_service_running_when_ps_outputs_container_id():
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")
    runner.run_results[("docker", "compose", "ps", "-q")] = RunResult(0, "abc123\n", "")

    mgr.refresh([meta("t1", [route])])

    assert mgr.service_running("t1", route) is True
    assert (("docker", "compose", "ps", "-q"), "/srv/app") in runner.run_calls


def test_docker_service_stopped_when_ps_output_empty():
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")
    runner.run_results[("docker", "compose", "ps", "-q")] = RunResult(0, "", "")

    mgr.refresh([meta("t1", [route])])

    assert mgr.service_running("t1", route) is False


def test_docker_service_command_failure_treated_as_stopped_and_kept_in_memory_only():
    # C4: 폴링 경로(refresh)의 도커 조회 실패는 로그 파일에 쓰지 않는다 -
    # 실패가 지속되면 매초 파일을 무한 성장시키기 때문이다. 대신 메모리
    # (docker_error())에만 남기고, main_window가 이를 툴팁으로 보여준다.
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")
    runner.run_results[("docker", "compose", "ps", "-q")] = RunResult(
        1, "", "docker: command not found")

    mgr.refresh([meta("t1", [route])])

    assert mgr.service_running("t1", route) is False
    assert "docker: command not found" in mgr.docker_error("t1", route)
    unit = reg.unit_service("t1", route.id)
    assert reg.log_path(unit) not in runner.files


def test_docker_service_start_stop_invokes_compose_and_survives_failure():
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")

    class BoomRunner(FakeRunner):
        def spawn_detached(self, cmd, cwd, log_path):
            raise RuntimeError("docker daemon down")

    boom = BoomRunner()
    boom.files = runner.files
    reg2 = RunRegistry(boom)
    mgr2 = ProcessManager(lambda: reg2)

    mgr2.start_service("t1", route)  # 예외가 UI로 전파되면 안 됨

    assert mgr2.service_running("t1", route) is False


# ---- refresh() 배치 조회 ----

def test_refresh_calls_pids_alive_exactly_once_regardless_of_unit_count():
    mgr, runner, reg = make_mgr()
    tunnels = []
    for i in range(5):
        name = f"t{i}"
        route = command_route(route_id=f"r{i}", start_cmd="cmd")
        t = meta(name, [route])
        reg.write_pid(reg.unit_tunnel(name), 100 + i)
        reg.write_pid(reg.unit_service(name, route.id), 200 + i)
        runner.live_pids.add(100 + i)
        tunnels.append(t)

    mgr.refresh(tunnels)

    assert runner.pids_alive_calls == 1


# ---- 러너 교체 시 상태 분리 ----

def test_refresh_reflects_current_registry_after_target_switch():
    runner_a = FakeRunner(home="/home/a")
    runner_b = FakeRunner(home="/home/b")
    reg_a = RunRegistry(runner_a)
    reg_b = RunRegistry(runner_b)
    current = {"reg": reg_a}
    mgr = ProcessManager(lambda: current["reg"])

    unit = reg_a.unit_tunnel("t1")
    reg_a.write_pid(unit, 1)
    runner_a.live_pids.add(1)
    runner_a.write_file(reg_a.log_path(unit), f"{RUNNING_MARKER}\n")
    mgr.refresh([meta("t1")])
    assert mgr.tunnel_state("t1") == TunnelState.RUNNING

    # 대상 전환: runner_b에는 t1의 PID 파일이 없음
    current["reg"] = reg_b
    mgr.refresh([meta("t1")])
    assert mgr.tunnel_state("t1") == TunnelState.STOPPED


def test_target_switch_does_not_leak_stale_marker_to_same_named_unit():
    # 로컬(A)의 "t1"이 마커까지 읽혀 RUNNING이 된 뒤, 원격(B)의 동명 "t1"으로
    # 전환한다. B는 PID가 있고 살아있지만 아직 마커가 없는 로그를 갖고 있다.
    # unit 이름("tunnel-t1")이 같으므로, 대상별로 캐시를 비우지 않으면 B가
    # 곧바로(스테일 마커 때문에) RUNNING으로 오판된다.
    runner_a = FakeRunner(home="/home/a")
    runner_b = FakeRunner(home="/home/b")
    reg_a = RunRegistry(runner_a)
    reg_b = RunRegistry(runner_b)
    current = {"reg": reg_a}
    mgr = ProcessManager(lambda: current["reg"])

    unit = reg_a.unit_tunnel("t1")
    reg_a.write_pid(unit, 1)
    runner_a.live_pids.add(1)
    runner_a.write_file(reg_a.log_path(unit), f"{RUNNING_MARKER}\n")
    mgr.refresh([meta("t1")])
    assert mgr.tunnel_state("t1") == TunnelState.RUNNING

    # B: PID 살아있음, 로그엔 아직 마커 없음
    reg_b.write_pid(unit, 2)
    runner_b.live_pids.add(2)
    runner_b.write_file(reg_b.log_path(unit), "connecting...\n")
    current["reg"] = reg_b
    mgr.refresh([meta("t1")])

    assert mgr.tunnel_state("t1") == TunnelState.STARTING  # RUNNING이면 안 됨


def test_target_switch_resets_log_offset_so_new_target_marker_is_found():
    # _log_offset도 unit 이름 기준이라 A의 오프셋으로 B의 로그를 시크하면
    # B의 진짜 마커를 건너뛸 수 있다. 대상 전환 시 오프셋도 초기화되어
    # B의 로그를 처음부터 다시 스캔해야 한다.
    runner_a = FakeRunner(home="/home/a")
    runner_b = FakeRunner(home="/home/b")
    reg_a = RunRegistry(runner_a)
    reg_b = RunRegistry(runner_b)
    current = {"reg": reg_a}
    mgr = ProcessManager(lambda: current["reg"])

    unit = reg_a.unit_tunnel("t1")
    reg_a.write_pid(unit, 1)
    runner_a.live_pids.add(1)
    # A의 로그를 B보다 길게 만들어 오프셋이 0보다 커지게 한다
    runner_a.write_file(reg_a.log_path(unit), "x" * 200 + f"\n{RUNNING_MARKER}\n")
    mgr.refresh([meta("t1")])
    assert mgr.tunnel_state("t1") == TunnelState.RUNNING

    # B: 짧은 로그의 맨 앞부분에 마커가 있음 (A의 오프셋으로 시크하면 건너뜀)
    reg_b.write_pid(unit, 2)
    runner_b.live_pids.add(2)
    runner_b.write_file(reg_b.log_path(unit), f"{RUNNING_MARKER}\n")
    current["reg"] = reg_b
    mgr.refresh([meta("t1")])

    assert mgr.tunnel_state("t1") == TunnelState.RUNNING  # B의 마커를 처음부터 찾아야 함


# ---- 로그 경로 ----

def test_log_path_helpers():
    mgr, runner, reg = make_mgr()
    route = command_route(route_id="r9")
    assert mgr.log_path_for_tunnel("t1") == reg.log_path(reg.unit_tunnel("t1"))
    assert (mgr.log_path_for_service("t1", route)
            == reg.log_path(reg.unit_service("t1", route.id)))


# ---- C3: PID 재사용 검증 ----

def test_reused_pid_with_mismatched_cmd_needs_three_consecutive_ticks_to_clear():
    # fail-open: npm->node exec 같은 오탐을 흡수하기 위해 1회 불일치로는
    # 절대 정리하지 않는다. 연속 3회 불일치가 확인돼야 재사용으로 판정한다.
    mgr, runner, reg = make_mgr()
    client = CloudflaredClient(runner)
    mgr.start_tunnel("t1", client)
    unit = reg.unit_tunnel("t1")
    pid = reg.read_pid(unit)
    assert pid in runner.live_pids

    # 재부팅 후 같은 PID가 완전히 무관한 프로세스에게 재배정된 상황을 흉내낸다:
    # PID는 살아있지만(운영체제가 재사용) 실제로 실행 중인 명령이 다르다.
    runner.pid_cmdlines_map[pid] = "unrelated-process"

    mgr.refresh([meta("t1")])
    assert reg.read_pid(unit) == pid  # 1회차: 아직 보존됨
    mgr.refresh([meta("t1")])
    assert reg.read_pid(unit) == pid  # 2회차: 아직 보존됨

    mgr.refresh([meta("t1")])  # 3회차: 재사용으로 확정

    # D2: 이름 불일치만으로는 절대 PID 파일을 지우지 않는다. 대신 ERROR로
    # 표시하고("확인 불가", STOPPED가 아님) PID 파일은 보존한다 - 사용자
    # 상태를 추측만으로 지우는 게 진짜 위험이기 때문이다.
    assert mgr.tunnel_state("t1") == TunnelState.ERROR
    assert reg.read_pid(unit) == pid  # PID 파일은 보존되어야 함
    assert mgr.tunnel_mismatch_reason("t1") is not None


def test_reused_pid_confirmed_mismatch_preserves_pid_and_stop_skips_kill():
    # 필수 회귀 테스트 (b)+(c): 3연속 불일치가 확정되면 PID 파일은 보존되고
    # ERROR + 사유가 노출되며, 그 상태에서 stop을 호출하면 kill_pid는 절대
    # 불리지 않고(남의 프로세스일 수 있으므로) PID 파일만 정리되어야 한다.
    mgr, runner, reg = make_mgr()
    client = CloudflaredClient(runner)
    mgr.start_tunnel("t1", client)
    unit = reg.unit_tunnel("t1")
    pid = reg.read_pid(unit)

    runner.pid_cmdlines_map[pid] = "totally-unrelated-command"
    for _ in range(3):
        mgr.refresh([meta("t1")])

    assert mgr.tunnel_state("t1") == TunnelState.ERROR
    assert reg.read_pid(unit) == pid
    reason = mgr.tunnel_mismatch_reason("t1")
    assert reason and "재사용" in reason
    assert pid in runner.live_pids  # 아직 "죽이지" 않았다

    mgr.stop_tunnel("t1")

    assert runner.kill_pid_calls == []  # kill_pid가 전혀 호출되지 않아야 함
    assert pid in runner.live_pids  # 그래서 여전히 살아있다(다른 프로세스니까)
    assert reg.read_pid(unit) is None  # 그러나 우리 쪽 PID 파일은 정리됨
    assert mgr.tunnel_state("t1") == TunnelState.STOPPED
    assert mgr.tunnel_mismatch_reason("t1") is None


def test_reused_pid_mismatch_streak_resets_on_match():
    # 연속 불일치 중간에 일치가 한 번이라도 나오면 정리로 이어지지 않는다.
    mgr, runner, reg = make_mgr()
    client = CloudflaredClient(runner)
    mgr.start_tunnel("t1", client)
    unit = reg.unit_tunnel("t1")
    pid = reg.read_pid(unit)
    correct_cmd = runner.pid_cmdlines_map[pid]

    runner.pid_cmdlines_map[pid] = "unrelated-process"
    mgr.refresh([meta("t1")])
    mgr.refresh([meta("t1")])
    assert reg.read_pid(unit) == pid  # 아직 2회, 정리 안 됨

    runner.pid_cmdlines_map[pid] = correct_cmd  # 다시 일치
    mgr.refresh([meta("t1")])
    assert reg.read_pid(unit) == pid

    runner.pid_cmdlines_map[pid] = "unrelated-process"
    mgr.refresh([meta("t1")])
    mgr.refresh([meta("t1")])
    assert reg.read_pid(unit) == pid  # 리셋되었으므로 이번에도 2회일 뿐


def test_alive_pid_with_matching_cmd_stays_running():
    mgr, runner, reg = make_mgr()
    client = CloudflaredClient(runner)
    mgr.start_tunnel("t1", client)
    unit = reg.unit_tunnel("t1")
    pid = reg.read_pid(unit)
    runner.write_file(reg.log_path(unit), f"{RUNNING_MARKER}\n")

    mgr.refresh([meta("t1")])  # spawn_detached가 자동으로 채운 cmd와 일치

    assert mgr.tunnel_state("t1") == TunnelState.RUNNING
    assert reg.read_pid(unit) == pid  # 정리되지 않아야 함


def test_verified_cmd_is_cached_and_not_rechecked_every_tick():
    # 중요도 #2: 이미 검증된 PID는 이후 tick에서 pid_cmdlines()를 다시
    # 부르지 않는다 (매 tick tasklist/ps 전체 조회를 없애는 캐시).
    mgr, runner, reg = make_mgr()
    client = CloudflaredClient(runner)
    mgr.start_tunnel("t1", client)

    mgr.refresh([meta("t1")])
    assert runner.pid_cmdlines_calls == 1

    mgr.refresh([meta("t1")])
    mgr.refresh([meta("t1")])
    assert runner.pid_cmdlines_calls == 1  # 캐시돼서 더 이상 호출 안 됨


def test_wrapper_prefix_cmd_match_npm_execs_node():
    # npm run dev가 실제로는 node로 보이는 경우 - 양방향 접두사 매칭이면
    # 자바스크립트 런타임 이름 차이도 흡수되진 않지만(완전 무관), 15자
    # 잘림이나 확장자 차이 같은 정상적인 케이스는 통과해야 한다.
    mgr, runner, reg = make_mgr()
    client = CloudflaredClient(runner)
    mgr.start_tunnel("t1", client)
    unit = reg.unit_tunnel("t1")
    pid = reg.read_pid(unit)
    # cloudflared의 comm이 15자 제한 등으로 잘려 보이는 상황을 흉내낸다
    runner.pid_cmdlines_map[pid] = "cloudflare"  # 저장된 토큰("cloudflared")의 접두사

    mgr.refresh([meta("t1")])

    assert mgr.tunnel_state("t1") != TunnelState.STOPPED
    assert reg.read_pid(unit) == pid


# ---- D1: 인터프리터/래퍼로 실행되는 명령의 args 포함 비교 ----

def test_cmd_tokens_match_interpreter_wrapped_args():
    # 필수 회귀 테스트 (a): SSH의 ps -o args=가 돌려주는 전체 커맨드라인에
    # "uvicorn"으로 등록된 토큰이 인터프리터 경로에 섞여 나와도(shebang이
    # /usr/bin/python3라서 comm은 "python3"이 되지만 args에는 uvicorn 경로가
    # 그대로 남는다) 일치로 판정되어야 한다.
    assert _cmd_tokens_match("uvicorn", "/usr/bin/python3 /usr/local/bin/uvicorn main:app")
    # npm run dev도 실제로는 node로 exec되지만 args엔 npm 경로가 남는다.
    assert _cmd_tokens_match("npm", "node /usr/bin/npm run dev")


def test_cmd_tokens_match_real_mismatch_still_fails():
    assert not _cmd_tokens_match("uvicorn", "/usr/sbin/sshd -D")


def test_alive_pid_with_interpreter_wrapped_args_stays_running_via_refresh():
    # 위 매칭 로직이 실제 refresh() 경로에서도 살아있는 것으로 유지시키는지
    # 확인한다 (고아가 되지 않아야 하는 게 이 Critical의 핵심). "uvicorn"으로
    # 등록된 서비스 유닛의 args가 인터프리터 경로("/usr/bin/python3
    # /usr/local/bin/uvicorn main:app")로 조회되는 상황을 흉내낸다.
    mgr, runner, reg = make_mgr()
    route = command_route(start_cmd="uvicorn")
    unit = reg.unit_service("t1", route.id)
    reg.write_pid(unit, 111, cmd="uvicorn")
    runner.live_pids.add(111)
    runner.pid_cmdlines_map[111] = "/usr/bin/python3 /usr/local/bin/uvicorn main:app"

    for _ in range(3):  # 3번 연속으로도 문제없이 계속 실행 중으로 유지되어야 한다
        mgr.refresh([meta("t1", [route])])
        assert mgr.service_running("t1", route) is True
        assert reg.read_pid(unit) == 111
        assert mgr.service_mismatch_reason("t1", route) is None


# ---- Critical: pid_cmdlines() 조회 실패 시 fail-open ----

def test_pid_cmdlines_lookup_failure_never_clears_pid_file():
    # 리뷰에서 지적된 fail-closed 버그의 핵심 회귀 테스트: pid_cmdlines()가
    # 조회 실패(None)를 반환하면(SSH 타임아웃, busybox ps -p/-o 미지원 등)
    # 명령 대조를 건너뛰고 PID 생존 판정만 써야 한다. 절대 PID 파일을
    # 지우면 안 된다 - 살아있는 프로세스를 고아로 만들면 안 되기 때문이다.
    mgr, runner, reg = make_mgr()
    client = CloudflaredClient(runner)
    mgr.start_tunnel("t1", client)
    unit = reg.unit_tunnel("t1")
    pid = reg.read_pid(unit)
    assert pid in runner.live_pids

    runner.pid_cmdlines_fail = True  # 조회 자체가 실패하도록 흉내

    for _ in range(5):  # 여러 tick 반복해도 정리되면 안 된다
        mgr.refresh([meta("t1")])
        assert reg.read_pid(unit) == pid  # (a) PID 파일이 남아 있다
        assert pid in runner.live_pids
        assert mgr.tunnel_state("t1") != TunnelState.STOPPED  # (b) 실행 중으로 유지


def test_pid_cmdlines_lookup_failure_then_recovery_still_verifies():
    # 조회가 한동안 실패하다 복구되면, 그 뒤로는 정상적으로 검증되어야 한다
    # (실패했던 tick의 카운터가 다음 성공 조회를 오염시키지 않아야 함).
    mgr, runner, reg = make_mgr()
    client = CloudflaredClient(runner)
    mgr.start_tunnel("t1", client)
    unit = reg.unit_tunnel("t1")
    pid = reg.read_pid(unit)

    runner.pid_cmdlines_fail = True
    mgr.refresh([meta("t1")])
    mgr.refresh([meta("t1")])
    assert reg.read_pid(unit) == pid

    runner.pid_cmdlines_fail = False  # 복구
    mgr.refresh([meta("t1")])

    assert reg.read_pid(unit) == pid  # 여전히 보존(정상 일치이므로)
    assert mgr.tunnel_state("t1") != TunnelState.STOPPED


def test_legacy_pid_file_without_cmd_token_only_checks_liveness():
    # 하위 호환: cmd 토큰 없이 PID만 적힌 옛 형식 파일은 생존 여부만 본다.
    mgr, runner, reg = make_mgr()
    unit = reg.unit_tunnel("t1")
    reg.write_pid(unit, 111)  # cmd 없음
    runner.live_pids.add(111)
    runner.write_file(reg.log_path(unit), f"{RUNNING_MARKER}\n")

    mgr.refresh([meta("t1")])

    assert mgr.tunnel_state("t1") == TunnelState.RUNNING
    assert reg.read_pid(unit) == 111
    assert runner.pid_cmdlines_calls == 0  # cmd 토큰이 없으니 조회할 필요 없음


def test_dead_pid_file_is_auto_cleared_after_error_tick():
    mgr, runner, reg = make_mgr()
    unit = reg.unit_tunnel("t1")
    reg.write_pid(unit, 111)  # live_pids에 없음 -> 죽어있음

    mgr.refresh([meta("t1")])
    assert mgr.tunnel_state("t1") == TunnelState.ERROR  # 이번 tick은 ERROR로 보여줌
    assert reg.read_pid(unit) is None  # 하지만 PID 파일은 이미 정리됨
    assert "PID" in runner.read_file(reg.log_path(unit))  # 원인 추적용 로그

    mgr.refresh([meta("t1")])
    assert mgr.tunnel_state("t1") == TunnelState.STOPPED  # 다음 tick부터 STOPPED


# ---- I5: 도커 낙관적 갱신 ----

def test_docker_start_service_optimistically_running_before_refresh():
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")

    mgr.start_service("t1", route)

    # refresh() 없이도 곧바로 실행 중으로 보여야 한다 (다음 refresh까지
    # 꺼진 채 보이는 I5 버그의 재발 방지)
    assert mgr.service_running("t1", route) is True


def test_docker_stop_service_optimistically_stopped_before_refresh():
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")
    runner.run_results[("docker", "compose", "ps", "-q")] = RunResult(0, "abc\n", "")
    mgr.refresh([meta("t1", [route])])
    assert mgr.service_running("t1", route) is True

    mgr.stop_service("t1", route)

    assert mgr.service_running("t1", route) is False


def test_docker_start_service_uses_spawn_detached_not_run():
    # 4-b: docker compose up --build -d는 몇 분 걸릴 수 있어 동기 run()으로
    # 실행하면 UI가 멈춘다. spawn_detached로 백그라운드 실행해야 한다.
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")
    route.server.start_cmd = ""  # 기본값(--build 포함) 사용

    mgr.start_service("t1", route)

    assert len(runner.spawn_detached_calls) == 1
    cmd, cwd, log_path = runner.spawn_detached_calls[0]
    assert cmd == ["docker", "compose", "up", "--build", "-d"]
    assert cwd == "/srv/app"
    unit = reg.unit_service("t1", route.id)
    # 도커 프로세스의 PID는 서비스 PID가 아니므로 PID 파일에 쓰지 않는다
    assert reg.read_pid(unit) is None
    # run()으로는 도커 명령을 실행하지 않는다
    assert not any(c[0][:3] == ("docker", "compose", "up") for c in runner.run_calls)


# ---- I6: 도커 시작/정지 명령이 저장된 값을 실제로 쓰는지 ----

def test_docker_start_service_uses_custom_start_cmd():
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")
    route.server.start_cmd = "docker compose -f prod.yml up -d"

    mgr.start_service("t1", route)

    assert (["docker", "compose", "-f", "prod.yml", "up", "-d"], "/srv/app") == \
        runner.spawn_detached_calls[0][:2]


def test_docker_start_service_falls_back_to_default_when_start_cmd_empty():
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")
    route.server.start_cmd = ""

    mgr.start_service("t1", route)

    assert (["docker", "compose", "up", "--build", "-d"], "/srv/app") == \
        runner.spawn_detached_calls[0][:2]


# ---- C4: 도커 폴링 실패는 로그 파일에 안 남고 메모리(docker_error)에만 ----

def test_docker_ps_failure_does_not_grow_log_file_across_ticks():
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")
    runner.run_results[("docker", "compose", "ps", "-q")] = RunResult(1, "", "boom")

    for _ in range(3):
        mgr._docker_checked_at.clear()  # 캐시를 매번 무효화해 실제로 재조회되게 함
        mgr.refresh([meta("t1", [route])])

    unit = reg.unit_service("t1", route.id)
    assert reg.log_path(unit) not in runner.files
    assert mgr.docker_error("t1", route) == "boom"


# ---- C2: 도커 조회 주기 분리(캐시) ----

def test_docker_ps_not_requeried_within_cache_window():
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")
    runner.run_results[("docker", "compose", "ps", "-q")] = RunResult(0, "abc\n", "")

    mgr.refresh([meta("t1", [route])])
    calls_after_first = len(runner.run_calls)
    mgr.refresh([meta("t1", [route])])  # 캐시 유효 기간 내 재호출

    assert len(runner.run_calls) == calls_after_first  # 추가 호출 없음
    assert mgr.service_running("t1", route) is True


# ---- 4-c: 전이(pending) 상태 ----

def test_tunnel_pending_clears_when_desired_state_reached():
    mgr, runner, reg = make_mgr()
    client = CloudflaredClient(runner)

    mgr.start_tunnel("t1", client)
    assert mgr.tunnel_pending("t1") is True

    mgr.refresh([meta("t1")])  # 실제로도 살아있음 -> desired(True)와 일치

    assert mgr.tunnel_pending("t1") is False


def test_tunnel_pending_clears_after_deadline_even_if_not_reached():
    mgr, runner, reg = make_mgr()
    unit = reg.unit_tunnel("t1")
    # STOPPED에서 정지 요청: desired=False이지만 PID가 여전히 남아있어
    # actual이 True인 상황을 흉내낸다(비정상 지연 시나리오).
    reg.write_pid(unit, 111)
    runner.live_pids.add(111)
    mgr._set_pending(unit, False, -1.0)  # 이미 지난 데드라인

    mgr.refresh([meta("t1")])

    assert mgr.tunnel_pending("t1") is False


def test_service_pending_clears_when_docker_actual_matches_desired():
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")

    mgr.start_service("t1", route)
    assert mgr.service_pending("t1", route) is True

    runner.run_results[("docker", "compose", "ps", "-q")] = RunResult(0, "abc\n", "")
    mgr.refresh([meta("t1", [route])])

    assert mgr.service_pending("t1", route) is False


def test_service_pending_command_kind_clears_on_match():
    mgr, runner, reg = make_mgr()
    route = command_route(start_cmd="myserver")

    mgr.start_service("t1", route)
    assert mgr.service_pending("t1", route) is True

    mgr.refresh([meta("t1", [route])])

    assert mgr.service_pending("t1", route) is False


# ---- 성능: 폴링 한 번의 원격 호출 수 ----

def test_refresh_uses_one_remote_call_for_pid_and_liveness():
    """PID 읽기와 생존 확인이 따로 나가면 느린 링크에서 왕복이 두 배가 된다."""
    from app.core.process_mgr import ProcessManager
    from app.core.run_registry import RunRegistry
    from app.core.store import RouteMeta, ServiceSpec, TunnelMeta, new_route_id

    class CountingRunner(FakeRunner):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.pid_probe_calls = 0

        def read_pid_files(self, paths):
            self.pid_probe_calls += 1
            return super().read_pid_files(paths)

    runner = CountingRunner(home="/home/pi")
    reg = RunRegistry(runner)
    mgr = ProcessManager(lambda: RunRegistry(runner))

    routes = [RouteMeta(id=new_route_id(), hostname=f"a{i}.example.com",
                        service="http://localhost:8000",
                        server=ServiceSpec(kind="command", start_cmd="run"))
              for i in range(3)]
    meta = TunnelMeta(name="t1", routes=routes)
    for unit in [reg.unit_tunnel("t1")] + [reg.unit_service("t1", r.id) for r in routes]:
        reg.write_pid(unit, 1000, cmd="run")

    mgr.refresh([meta])

    # 유닛이 4개여도 조회는 한 번
    assert runner.pid_probe_calls == 1


def test_read_records_alive_marks_only_live_units():
    from app.core.run_registry import RunRegistry

    runner = FakeRunner(home="/home/pi")
    reg = RunRegistry(runner)
    reg.write_pid("a", 111, cmd="x")
    reg.write_pid("b", 222, cmd="x")
    runner.live_pids = {111}

    records, alive = reg.read_records_alive(["a", "b", "없는유닛"])

    assert records["a"] == (111, "x")
    assert records["b"] == (222, "x")
    assert records["없는유닛"] is None
    assert alive == {"a"}
