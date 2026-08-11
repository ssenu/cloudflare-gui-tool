from __future__ import annotations

from app.core.runner import CommandRunner


class RunRegistry:
    """대상 머신의 ``~/.cloudflare-gui/run/`` 아래 PID·로그 파일을 관리한다.

    상태는 메모리가 아니라 대상 머신의 디스크에 있다: PID 파일이 존재하면
    "이 단위는 실행 중이어야 한다"는 선언이고, 실제 생존 여부는 매 폴링마다
    runner로 확인한다. 이 클래스는 생성 시 받은 runner 하나에 대해서만
    유효하다 — 대상(로컬<->SSH)이 바뀌면 새 RunRegistry를 만들어야 한다.
    """

    def __init__(self, runner: CommandRunner):
        self.runner = runner

    def run_dir(self) -> str:
        # CloudflaredClient.config_dir()과 동일하게 역슬래시를 슬래시로
        # 정규화한다 (Windows 홈 경로 대비, SFTP/원격 경로와 형식 통일).
        return self.runner.home_dir().replace("\\", "/") + "/.cloudflare-gui/run"

    # ---- 단위 이름 ----
    def unit_tunnel(self, name: str) -> str:
        return f"tunnel-{name}"

    def unit_service(self, tunnel: str, route_id: str) -> str:
        return f"svc-{tunnel}-{route_id}"

    # ---- 경로 ----
    def pid_path(self, unit: str) -> str:
        return f"{self.run_dir()}/{unit}.pid"

    def log_path(self, unit: str) -> str:
        return f"{self.run_dir()}/{unit}.log"

    # ---- PID 파일 ----
    def write_pid(self, unit: str, pid: int) -> None:
        self.runner.ensure_dir(self.run_dir())
        self.runner.write_file(self.pid_path(unit), str(pid))

    def read_pid(self, unit: str) -> int | None:
        path = self.pid_path(unit)
        if not self.runner.file_exists(path):
            return None
        try:
            return int(self.runner.read_file(path).strip())
        except (ValueError, OSError):
            # 내용이 없거나 정수가 아니면 무효한 PID 파일로 취급
            return None

    def clear_pid(self, unit: str) -> None:
        path = self.pid_path(unit)
        if self.runner.file_exists(path):
            self.runner.remove_file(path)

    def rotate_log_if_big(self, unit: str, limit: int = 5_000_000) -> None:
        """로그가 limit을 넘으면 직전 내용을 ``<unit>.log.1``로 옮기고 비운다.

        구현 선택: runner에는 rename이 없다. read + write(.log.1) + remove(원본)
        방식으로 "이전 내용 보존"이라는 스펙 요구를 충족시킨다. 파일 전체를
        한 번 더 읽는 비용이 들지만, 이 함수는 새 실행을 시작할 때 딱 한 번,
        그것도 이미 5MB를 넘은 드문 경우에만 호출되므로 감수할 만하다.
        (대안으로 그냥 비우기만 하는 방법도 있었지만 직전 로그를 완전히
        잃게 되어 스펙의 ".log.1로 이동"과 어긋나 채택하지 않았다.)
        """
        path = self.log_path(unit)
        if not self.runner.file_exists(path):
            return
        try:
            content = self.runner.read_file(path)
        except (OSError, UnicodeDecodeError):
            # 읽을 수 없는 로그는 로테이션을 건너뛴다 (다음 기회에 재시도)
            return
        if len(content.encode("utf-8", errors="replace")) <= limit:
            return
        self.runner.write_file(path + ".1", content)
        self.runner.remove_file(path)

    def known_units(self) -> list[str]:
        """run 디렉터리를 스캔해 ``*.pid`` 파일에서 단위 이름 목록을 만든다."""
        names = self.runner.list_dir(self.run_dir())
        return [n[:-4] for n in names if n.endswith(".pid")]

    def alive_map(self, units: list[str]) -> dict[str, bool]:
        """단위별 PID 생존 여부를 한 번의 pids_alive 호출로 일괄 확인한다."""
        pid_by_unit = {u: self.read_pid(u) for u in units}
        pids = [p for p in pid_by_unit.values() if p is not None]
        alive_pids = self.runner.pids_alive(pids)
        return {
            u: (p is not None and p in alive_pids)
            for u, p in pid_by_unit.items()
        }
