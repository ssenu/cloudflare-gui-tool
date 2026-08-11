from __future__ import annotations

from app.core.cloudflared import CloudflaredClient
from app.core.process_mgr import ProcessManager
from app.core.run_registry import RunRegistry
from app.core.runner import CommandRunner, LocalRunner
from app.core.ssh_runner import SshRunner
from app.core.store import SettingsStore, SshProfile


class AppContext:
    def __init__(self, store: SettingsStore | None = None):
        self.store = store or SettingsStore()
        self.store.load()
        self.local_runner = LocalRunner()
        self.runner: CommandRunner = self.local_runner
        self._remote: SshRunner | None = None
        # registry_provider: 항상 현재 ctx.runner(대상 전환 반영)를 감싼
        # RunRegistry를 새로 만들어 돌려준다 - ProcessManager는 러너를
        # 캐시하지 않으므로 set_local()/set_remote() 이후에도 최신 대상을 본다.
        self.manager = ProcessManager(lambda: RunRegistry(self.runner))

    def _binary(self) -> str:
        if not self.is_remote and self.store.settings.cloudflared_path:
            return self.store.settings.cloudflared_path
        return "cloudflared"

    @property
    def client(self) -> CloudflaredClient:
        return CloudflaredClient(self.runner, binary=self._binary())

    @property
    def is_remote(self) -> bool:
        return self.runner is not self.local_runner

    def set_local(self) -> None:
        if self._remote:
            self._remote.close()
            self._remote = None
        self.runner = self.local_runner

    def set_remote(self, profile: SshProfile, password: str | None = None) -> None:
        remote = SshRunner(profile, password=password)
        remote.connect()  # 실패 시 예외 → 호출측에서 다이얼로그 표시
        if self._remote:
            self._remote.close()
        self._remote = remote
        self.runner = remote
