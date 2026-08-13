from __future__ import annotations

import logging
import time

from app.core.runner import CommandRunner

logger = logging.getLogger(__name__)


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

    def unit_clone(self, repo_id: str) -> str:
        return f"clone-{repo_id}"

    # ---- 경로 ----
    def pid_path(self, unit: str) -> str:
        return f"{self.run_dir()}/{unit}.pid"

    def log_path(self, unit: str) -> str:
        return f"{self.run_dir()}/{unit}.log"

    # ---- PID 파일 ----
    def write_pid(self, unit: str, pid: int, cmd: str | None = None) -> None:
        """PID 파일을 쓴다.

        cmd가 주어지면 검증용 토큰(실행 명령의 첫 토큰, 예: ``cloudflared``)과
        시작 시각을 함께 기록해 재부팅 후 PID 재사용을 걸러낼 수 있게 한다.
        형식: 1행 PID, 2행 ``cmd=<토큰>``, 3행 ``started=<epoch 초>``.
        cmd가 없으면(예: stop_cmd 실행형 서비스 등) 기존처럼 PID 한 줄만 쓴다 -
        이 경우 생존 판정은 PID 존재 여부만으로 이루어진다.

        D3: ``started=``는 현재 어디서도 읽지 않는다(죽은 데이터). 지우지
        않고 남겨둔 이유는, D2(명령 불일치 3연속 -> ERROR)로도 못 잡는
        경우 - 예를 들어 재사용된 PID에 우연히 같은 이름의 명령이 떠 있는
        경우 - 를 걸러낼 다음 방어선으로 쓸 계획이기 때문이다: 대상에서
        실제 프로세스 시작 시각(``ps -o lstart=``/``stat`` 등)을 조회해
        이 기록된 시각과 비교하면 이름이 같아도 그 사이에 재시작되었는지
        구분할 수 있다. 지금 당장 쓰지 않는다고 형식에서 빼면, 이미 이
        필드로 마이그레이션된 기존 PID 파일들과의 호환성만 잃는다.
        """
        self.runner.ensure_dir(self.run_dir())
        lines = [str(pid)]
        if cmd:
            lines.append(f"cmd={cmd}")
            lines.append(f"started={int(time.time())}")
        self.runner.write_file(self.pid_path(unit), "\n".join(lines))

    def read_pid(self, unit: str) -> int | None:
        record = self.read_record(unit)
        return record[0] if record else None

    def read_records_alive(self, units: list[str]) -> tuple[
            dict[str, tuple[int, str | None] | None], set[str]]:
        """여러 유닛의 PID 기록과 생존 여부를 한 번에 읽는다.

        반환: ({유닛: (pid, cmd) 또는 None}, 살아있는 유닛 집합)
        """
        if not units:
            return {}, set()
        paths = [self.pid_path(u) for u in units]
        probed = self.runner.read_pid_files(paths)
        records: dict[str, tuple[int, str | None] | None] = {}
        alive: set[str] = set()
        for unit, path in zip(units, paths):
            content, is_alive = probed.get(path, (None, False))
            records[unit] = self._parse_record(content, path)
            if is_alive and records[unit] is not None:
                alive.add(unit)
        return records, alive

    def read_records(self, units: list[str]) -> dict[str, tuple[int, str | None] | None]:
        """여러 유닛의 PID 기록을 한 번에 읽는다(원격 왕복 N -> 1)."""
        if not units:
            return {}
        contents = self.runner.read_files([self.pid_path(u) for u in units])
        out: dict[str, tuple[int, str | None] | None] = {}
        for unit in units:
            out[unit] = self._parse_record(contents.get(self.pid_path(unit)),
                                           self.pid_path(unit))
        return out

    def read_record(self, unit: str) -> tuple[int, str | None] | None:
        """PID 파일을 (pid, cmd 토큰) 형태로 읽는다. cmd가 없으면 None.

        file_exists() + read_file() 왕복 두 번 대신, 읽기를 먼저 시도하고
        없으면 예외를 잡는 방식으로 원격(SFTP) 왕복을 한 번으로 줄인다.
        """
        path = self.pid_path(unit)
        try:
            content = self.runner.read_file(path)
        except FileNotFoundError:
            return None
        except UnicodeDecodeError:
            # 깨진 PID 파일 - 연결 문제가 아니므로 배너/백오프를 유발하면
            # 안 된다. "없음"으로 취급하되 원인 추적을 위해 로그만 남긴다.
            logger.warning("PID 파일 디코드 실패, 없음으로 취급: %s", path)
            return None
        # socket.timeout 등 다른 OSError는 여기서 삼키지 않고 그대로
        # 올려보낸다 - _tick()이 연결 오류 배너를 띄우고 백오프하도록.
        return self._parse_record(content, path)

    @staticmethod
    def _parse_record(content: str | None, path: str) -> tuple[int, str | None] | None:
        """PID 파일 내용을 (pid, cmd 토큰)으로 해석한다. 무효하면 None."""
        if not content:
            return None
        lines = content.splitlines()
        if not lines:
            return None
        try:
            pid = int(lines[0].strip())
        except ValueError:
            # 내용이 없거나 정수가 아니면 무효한 PID 파일로 취급
            return None
        cmd: str | None = None
        for line in lines[1:]:
            if line.startswith("cmd="):
                cmd = line[len("cmd="):].strip()
                break
        return pid, cmd or None

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
