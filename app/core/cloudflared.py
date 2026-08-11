from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.core.runner import CommandRunner, RunResult

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                     re.IGNORECASE)


@dataclass
class TunnelInfo:
    id: str
    name: str
    created_at: str
    connections: int


class CloudflaredError(Exception):
    def __init__(self, result: RunResult):
        self.result = result
        super().__init__(result.stderr.strip() or result.stdout.strip()
                         or f"exit code {result.exit_code}")


class CloudflaredClient:
    def __init__(self, runner: CommandRunner, binary: str = "cloudflared"):
        self.runner = runner
        self.binary = binary

    def _run(self, args: list[str], timeout: float = 120.0) -> RunResult:
        res = self.runner.run([self.binary] + args, timeout=timeout)
        if res.exit_code != 0:
            raise CloudflaredError(res)
        return res

    def version(self) -> str | None:
        try:
            res = self.runner.run([self.binary, "--version"], timeout=15.0)
        except Exception:
            # TimeoutExpired 등 예상 못한 예외까지 포함해 미설치/응답없음으로 취급
            return None
        return res.stdout.strip() if res.exit_code == 0 else None

    def config_dir(self) -> str:
        return self.runner.home_dir().replace("\\", "/") + "/.cloudflared"

    def cert_path(self) -> str:
        return self.config_dir() + "/cert.pem"

    def cert_exists(self) -> bool:
        return self.runner.file_exists(self.cert_path())

    def config_path(self, name: str) -> str:
        return self.config_dir() + f"/config-{name}.yml"

    def list_tunnels(self) -> list[TunnelInfo]:
        res = self._run(["tunnel", "list", "--output", "json"])
        try:
            # 터널이 0개면 cloudflared가 "null"을 출력함 → 빈 목록으로 처리
            data = json.loads(res.stdout or "[]") or []
        except json.JSONDecodeError:
            raise CloudflaredError(RunResult(1, res.stdout,
                                             "터널 목록 JSON 파싱 실패"))
        return [TunnelInfo(id=t["id"], name=t["name"],
                           created_at=t.get("created_at", ""),
                           connections=len(t.get("connections") or []))
                for t in data]

    def create_tunnel(self, name: str) -> tuple[str, str]:
        res = self._run(["tunnel", "create", name])
        text = res.stdout + res.stderr
        m = UUID_RE.search(text)
        if not m:
            raise CloudflaredError(RunResult(1, res.stdout,
                                             "터널 ID를 출력에서 찾지 못했습니다"))
        tid = m.group(0)
        return tid, self.config_dir() + f"/{tid}.json"

    def delete_tunnel(self, name: str) -> None:
        self._run(["tunnel", "delete", name])

    def route_dns(self, name: str, hostname: str) -> None:
        self._run(["tunnel", "route", "dns", name, hostname])

    def run_args(self, name: str) -> list[str]:
        return [self.binary, "--config", self.config_path(name),
                "tunnel", "run", name]

    def login_args(self) -> list[str]:
        return [self.binary, "tunnel", "login"]
