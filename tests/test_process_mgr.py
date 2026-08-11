from app.core.cloudflared import CloudflaredClient
from app.core.process_mgr import RUNNING_MARKER, ProcessManager, TunnelState
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


def test_docker_service_command_failure_treated_as_stopped_and_logged():
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")
    runner.run_results[("docker", "compose", "ps", "-q")] = RunResult(
        1, "", "docker: command not found")

    mgr.refresh([meta("t1", [route])])

    assert mgr.service_running("t1", route) is False
    unit = reg.unit_service("t1", route.id)
    assert "docker: command not found" in runner.read_file(reg.log_path(unit))


def test_docker_service_start_stop_invokes_compose_and_survives_failure():
    mgr, runner, reg = make_mgr()
    route = docker_route(cwd="/srv/app")
    runner.run_raises[("docker", "compose", "up", "-d")] = RuntimeError("docker daemon down")

    mgr.start_service("t1", route)  # 예외가 UI로 전파되면 안 됨

    unit = reg.unit_service("t1", route.id)
    # start_service의 run()은 refresh()와 달리 예외를 그대로 잡지 않을 수 있으므로
    # 최소한 상태 조회 자체는 안전해야 한다
    assert mgr.service_running("t1", route) is False


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
