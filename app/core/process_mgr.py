from __future__ import annotations

import os
import shlex
import time
from enum import Enum, auto
from typing import Callable

from app.core.cloudflared import CloudflaredClient
from app.core.run_registry import RunRegistry
from app.core.store import RouteMeta, TunnelMeta

RUNNING_MARKER = "Registered tunnel connection"

# 폴링 경로(refresh()) 전용 원격 명령 timeout. 사용자가 누른 start/stop은
# CommandRunner의 기본 timeout(60초)을 그대로 쓴다 - 여기서 짧게 자르면
# 안 그래도 느린 원격 명령이 실패로 오판될 수 있기 때문이다.
POLL_TIMEOUT = 5.0

# 도커 compose ps 조회 캐시 유효 시간(초). 1초 폴링마다 매번 원격 명령을
# 왕복시키지 않기 위해 이 시간 동안은 직전 값을 재사용한다.
DOCKER_POLL_INTERVAL = 5.0

DOCKER_START_DEFAULT = ["docker", "compose", "up", "-d"]
DOCKER_STOP_DEFAULT = ["docker", "compose", "down"]


class TunnelState(Enum):
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    ERROR = auto()


def _split_cmd(cmd: str) -> list[str]:
    # posix=False: Windows 경로의 역슬래시를 보존한다. 이후 따옴표만 벗겨준다.
    parts = shlex.split(cmd, posix=False)
    return [p.strip('"') for p in parts]


def _cmd_token(argv: list[str]) -> str:
    """실행 명령의 첫 토큰(실행 파일 이름)을 PID 검증용 태그로 뽑는다.

    경로 전체가 아니라 basename만 쓰는 이유는 ps -o comm=/tasklist가 돌려주는
    값도 짧은 이름(리눅스는 최대 15자로 잘리기도 함)이라, 포함(in) 비교로
    맞춰보려면 우리 쪽도 짧은 이름이어야 하기 때문이다.
    """
    if not argv:
        return ""
    return os.path.basename(argv[0].strip('"'))


class ProcessManager:
    """PID·로그 파일 기반으로 터널/서비스 상태를 판정한다 (v2).

    메모리에 프로세스 핸들을 들고 있지 않는다 — 진짜 상태는 대상 머신의
    run 디렉터리(RunRegistry)에 있고, 이 클래스는 그것을 읽고 쓸 뿐이다.

    생성자 형태: ``ProcessManager(registry_provider)``. registry_provider는
    "현재 대상의 RunRegistry"를 돌려주는 콜러블이다. UI는 ``ctx.runner``를
    바꿔 대상(로컬<->SSH)을 전환하는데, ProcessManager가 러너를 필드로
    캐시해버리면 대상 전환 후에도 옛 러너를 계속 바라보는 문제가 생긴다.
    provider를 매 호출마다 통해서 현재 registry(및 그 안의 runner)를
    다시 얻으면 이 문제가 사라진다 — ProcessManager를 재생성할 필요도 없다.

    상태 판정은 refresh()가 채운 캐시를 읽는다. 개별 조회 메서드
    (tunnel_state/service_running)는 캐시만 보고 runner를 직접 두드리지
    않는다 — UI가 1초마다 refresh()를 호출하고 여러 카드가 그 결과를
    나눠 읽는 구조를 전제로 한다. start_tunnel/stop_tunnel 등 상태를
    바꾸는 동작은 다음 refresh()를 기다리지 않도록 캐시를 낙관적으로
    갱신한다(정지 직후 바로 STOPPED로 보이는 것을 보장하기 위함).
    """

    def __init__(self, registry_provider: Callable[[], RunRegistry]):
        self._registry_provider = registry_provider
        # unit -> (has_pid, alive)
        self._alive: dict[str, tuple[bool, bool]] = {}
        # unit -> RUNNING_MARKER를 이 실행에서 본 적 있는지
        self._marker_seen: dict[str, bool] = {}
        # unit -> 다음 refresh에서 tail_file을 이어 읽을 오프셋
        self._log_offset: dict[str, int] = {}
        # unit -> 도커 서비스 실행 여부 (refresh()의 ps -q 결과, 또는 낙관적 갱신값)
        self._docker_running: dict[str, bool] = {}
        # unit -> 마지막으로 docker compose ps를 실제로 호출한 시각(time.time()).
        # DOCKER_POLL_INTERVAL초 이내면 재조회하지 않고 _docker_running을 재사용한다.
        self._docker_checked_at: dict[str, float] = {}
        # unit -> 마지막 도커 조회 실패 사유(메모리만, 로그 파일에는 안 남김 - C4)
        self._docker_error: dict[str, str] = {}
        # 직전 refresh()가 어느 대상(run 디렉터리)을 봤는지. 로컬/원격에
        # 동명 터널이 있을 수 있으므로 unit 이름만으로는 대상을 구분할 수
        # 없다 - run_dir()이 바뀌면 대상이 전환된 것이므로 캐시를 전부
        # 비우고 다시 채운다. runner.name이 아니라 run_dir()을 쓰는 이유는
        # 같은 러너 종류라도 대상 머신(홈 디렉터리)이 다르면 다른 run
        # 디렉터리를 갖기 때문이다.
        self._last_run_dir: str | None = None

    def _registry(self) -> RunRegistry:
        reg = self._registry_provider()
        self._ensure_target(reg)
        return reg

    def _ensure_target(self, reg: RunRegistry) -> None:
        """캐시가 reg가 가리키는 대상 것인지 확인하고, 아니면 비운다.

        로컬/원격에 동명 터널·서비스가 있으면 unit 이름이 겹치므로, 대상이
        바뀔 때마다(run_dir()이 달라지면) 옛 대상의 캐시가 새 대상에
        새어 들어가지 않도록 전부 지운다. start_tunnel처럼 refresh() 없이
        캐시를 직접 쓰는 메서드도 있으므로 _registry()를 거치는 모든
        경로(즉 이 클래스의 모든 공개 메서드)에서 공통으로 호출된다.
        """
        run_dir = reg.run_dir()
        if run_dir == self._last_run_dir:
            return
        self._alive.clear()
        self._marker_seen.clear()
        self._log_offset.clear()
        self._docker_running.clear()
        self._docker_checked_at.clear()
        self._docker_error.clear()
        self._last_run_dir = run_dir

    # ---- 터널 ----
    def start_tunnel(self, name: str, client: CloudflaredClient) -> None:
        reg = self._registry()
        unit = reg.unit_tunnel(name)
        reg.rotate_log_if_big(unit)
        log_path = reg.log_path(unit)
        args = client.run_args(name)
        pid = reg.runner.spawn_detached(args, None, log_path)
        reg.write_pid(unit, pid, cmd=_cmd_token(args))
        # 시작 시점 로그 오프셋을 기록해 RUNNING 마커 탐색이 이번 실행의
        # 출력만 보도록 한다 (로테이션 안 된 기존 로그에 이전 실행의
        # 마커가 남아 있어도 오탐하지 않는다).
        offset, _ = reg.runner.tail_file(log_path, 0)
        self._log_offset[unit] = offset
        self._marker_seen[unit] = False
        self._alive[unit] = (True, True)

    def stop_tunnel(self, name: str) -> None:
        reg = self._registry()
        unit = reg.unit_tunnel(name)
        pid = reg.read_pid(unit)
        if pid is not None:
            reg.runner.kill_pid(pid)
        reg.clear_pid(unit)
        self._alive[unit] = (False, False)
        self._marker_seen[unit] = False

    def tunnel_state(self, name: str) -> TunnelState:
        reg = self._registry()
        unit = reg.unit_tunnel(name)
        has_pid, alive = self._alive.get(unit, (False, False))
        if not has_pid:
            return TunnelState.STOPPED
        if not alive:
            return TunnelState.ERROR
        if self._marker_seen.get(unit, False):
            return TunnelState.RUNNING
        return TunnelState.STARTING

    # ---- 서비스 ----
    def start_service(self, tunnel: str, route: RouteMeta) -> None:
        reg = self._registry()
        unit = reg.unit_service(tunnel, route.id)
        server = route.server
        if server.kind == "docker":
            cmd = _split_cmd(server.start_cmd) if server.start_cmd.strip() else DOCKER_START_DEFAULT
            # I5: 사용자가 켠 토글이 다음 refresh까지 꺼진 채 보이지 않도록
            # 낙관적으로 갱신하고, 캐시 만료를 리셋해 다음 refresh()가 곧바로
            # 실제 상태로 확정하게 한다. 명령이 실패/예외면 낙관적으로도
            # "실행 중"이라 하지 않는다 - 애초에 안 켜졌을 가능성이 높다.
            try:
                res = reg.runner.run(cmd, cwd=server.cwd)
                if res.exit_code != 0:
                    self._append_log(reg, unit,
                                     f"[오류] docker compose up 실패: "
                                     f"{res.stderr.strip() or res.stdout.strip()}")
                    self._docker_running[unit] = False
                else:
                    self._docker_running[unit] = True
            except Exception as exc:  # 도커 미설치 등도 UI로 전파하지 않는다
                self._append_log(reg, unit, f"[오류] docker compose up 실패: {exc}")
                self._docker_running[unit] = False
            self._docker_checked_at[unit] = 0.0
            return
        reg.rotate_log_if_big(unit)
        log_path = reg.log_path(unit)
        cmd = _split_cmd(server.start_cmd)
        pid = reg.runner.spawn_detached(cmd, server.cwd or None, log_path)
        reg.write_pid(unit, pid, cmd=_cmd_token(cmd))
        self._alive[unit] = (True, True)

    def stop_service(self, tunnel: str, route: RouteMeta) -> None:
        reg = self._registry()
        unit = reg.unit_service(tunnel, route.id)
        server = route.server
        if server.kind == "docker":
            cmd = _split_cmd(server.stop_cmd) if server.stop_cmd.strip() else DOCKER_STOP_DEFAULT
            try:
                res = reg.runner.run(cmd, cwd=server.cwd)
                if res.exit_code != 0:
                    self._append_log(reg, unit,
                                     f"[오류] docker compose down 실패: "
                                     f"{res.stderr.strip() or res.stdout.strip()}")
            except Exception as exc:
                self._append_log(reg, unit, f"[오류] docker compose down 실패: {exc}")
            self._docker_running[unit] = False
            self._docker_checked_at[unit] = 0.0
            return
        if server.stop_cmd:
            cmd = _split_cmd(server.stop_cmd)
            reg.runner.run(cmd, cwd=server.cwd or None)
        else:
            pid = reg.read_pid(unit)
            if pid is not None:
                reg.runner.kill_pid(pid)
        reg.clear_pid(unit)
        self._alive[unit] = (False, False)

    def service_running(self, tunnel: str, route: RouteMeta) -> bool:
        reg = self._registry()
        unit = reg.unit_service(tunnel, route.id)
        if route.server.kind == "docker":
            return self._docker_running.get(unit, False)
        has_pid, alive = self._alive.get(unit, (False, False))
        return has_pid and alive

    def docker_error(self, tunnel: str, route: RouteMeta) -> str | None:
        """가장 최근 docker compose ps 조회 실패 사유(메모리만, I5/C4용)."""
        reg = self._registry()
        unit = reg.unit_service(tunnel, route.id)
        return self._docker_error.get(unit)

    # ---- 로그 경로 ----
    def log_path_for_tunnel(self, name: str) -> str:
        reg = self._registry()
        return reg.log_path(reg.unit_tunnel(name))

    def log_path_for_service(self, tunnel: str, route: RouteMeta) -> str:
        reg = self._registry()
        return reg.log_path(reg.unit_service(tunnel, route.id))

    def _remove_log_files(self, reg: RunRegistry, unit: str) -> None:
        for path in (reg.log_path(unit), reg.log_path(unit) + ".1"):
            try:
                if reg.runner.file_exists(path):
                    reg.runner.remove_file(path)
            except Exception:
                pass

    def cleanup_logs_for_tunnel(self, name: str) -> None:
        reg = self._registry()
        self._remove_log_files(reg, reg.unit_tunnel(name))

    def cleanup_logs_for_service(self, tunnel: str, route: RouteMeta) -> None:
        reg = self._registry()
        self._remove_log_files(reg, reg.unit_service(tunnel, route.id))

    # ---- 폴링 ----
    def refresh(self, tunnels: list[TunnelMeta]) -> None:
        """단위 수와 무관하게 PID 생존 확인은 pids_alive() 한 번으로 끝낸다."""
        reg = self._registry()  # 대상 전환 시 _ensure_target()이 캐시를 비운다

        tunnel_units = [reg.unit_tunnel(t.name) for t in tunnels]
        command_units: list[str] = []
        docker_units: list[tuple[str, str]] = []  # (unit, cwd)
        for t in tunnels:
            for r in t.routes:
                unit = reg.unit_service(t.name, r.id)
                if r.server.kind == "docker":
                    docker_units.append((unit, r.server.cwd))
                else:
                    command_units.append(unit)

        pid_units = tunnel_units + command_units
        # C3: PID뿐 아니라 기록된 cmd 토큰까지 함께 읽는다 (재사용 검증용).
        records = {u: reg.read_record(u) for u in pid_units}
        pids = [rec[0] for rec in records.values() if rec is not None]
        alive_pids = reg.runner.pids_alive(pids, timeout=POLL_TIMEOUT)  # 호출 1회

        cmd_check_pids = [rec[0] for rec in records.values()
                          if rec is not None and rec[1] and rec[0] in alive_pids]
        cmdlines = reg.runner.pid_cmdlines(cmd_check_pids) if cmd_check_pids else {}

        for unit, rec in records.items():
            if rec is None:
                self._alive[unit] = (False, False)
                continue
            pid, cmd_token = rec
            alive = pid in alive_pids
            if alive and cmd_token:
                actual = cmdlines.get(pid, "")
                if cmd_token not in actual:
                    # PID 재사용: 저장된 cmd와 실제 실행 중인 명령이 다르므로
                    # 남의 프로세스를 우리 것으로 오판하지 않도록 즉시 중지로
                    # 판정하고 PID 파일을 정리한다.
                    self._append_log(
                        reg, unit,
                        f"[정보] PID {pid}가 재사용된 것으로 보여(다른 명령 실행 중) "
                        "중지 상태로 판정하고 PID 파일을 정리합니다.")
                    reg.clear_pid(unit)
                    self._alive[unit] = (False, False)
                    continue
            if not alive:
                # 죽은 PID 파일을 자동 정리한다(값싼 minor). 이번 tick은
                # ERROR로 보여주고(has_pid=True, alive=False), 파일은 지워
                # 다음 tick부터 STOPPED가 되게 한다. 원인 추적용으로 로그를
                # 남긴다.
                self._append_log(
                    reg, unit, f"[정보] PID {pid}가 죽어 있어 PID 파일을 정리합니다.")
                reg.clear_pid(unit)
            self._alive[unit] = (True, alive)

        for unit in tunnel_units:
            has_pid, alive = self._alive[unit]
            if not (has_pid and alive):
                continue
            base = self._log_offset.get(unit, 0)
            new_offset, text = reg.runner.tail_file(reg.log_path(unit), base)
            if RUNNING_MARKER in text:
                self._marker_seen[unit] = True
            self._log_offset[unit] = new_offset

        now = time.time()
        for unit, cwd in docker_units:
            last = self._docker_checked_at.get(unit, 0.0)
            if now - last < DOCKER_POLL_INTERVAL:
                continue  # 캐시 유효: 직전 self._docker_running 값을 재사용
            try:
                res = reg.runner.run(["docker", "compose", "ps", "-q"], cwd=cwd,
                                     timeout=POLL_TIMEOUT)
                running = res.exit_code == 0 and bool(res.stdout.strip())
                if res.exit_code != 0:
                    # C4: 폴링 경로 실패는 파일에 남기지 않고 메모리에만 보관한다
                    # (도커 유닛은 로그 로테이션 대상도 아니라 무한 성장 위험이 큼).
                    self._docker_error[unit] = res.stderr.strip() or res.stdout.strip()
                else:
                    self._docker_error.pop(unit, None)
            except Exception as exc:  # 도커 미설치 등 예상 못한 예외도 "중지"로 간주
                running = False
                self._docker_error[unit] = str(exc)
            self._docker_running[unit] = running
            self._docker_checked_at[unit] = now

    def _append_log(self, reg: RunRegistry, unit: str, text: str) -> None:
        reg.runner.append_file(reg.log_path(unit), text + "\n")
