from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field


@dataclass
class TunnelMeta:
    name: str
    hostname: str = ""
    service: str = ""
    server_cmd: str = ""
    server_cwd: str = ""
    start_together: bool = False


@dataclass
class SshProfile:
    name: str
    host: str
    port: int = 22
    username: str = "pi"
    key_path: str = ""


@dataclass
class Settings:
    root_domain: str = ""
    cloudflared_path: str = ""
    tunnels: dict[str, TunnelMeta] = field(default_factory=dict)
    ssh_profiles: list[SshProfile] = field(default_factory=list)


def default_settings_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "CloudflareTunnelGUI", "settings.json")


class SettingsStore:
    def __init__(self, path: str | None = None):
        self.path = path or default_settings_path()
        self.settings = Settings()

    def load(self) -> Settings:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            self.settings = Settings(
                root_domain=raw.get("root_domain", ""),
                cloudflared_path=raw.get("cloudflared_path", ""),
                tunnels={k: TunnelMeta(**v) for k, v in raw.get("tunnels", {}).items()},
                ssh_profiles=[SshProfile(**p) for p in raw.get("ssh_profiles", [])],
            )
        return self.settings

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        raw = {
            "root_domain": self.settings.root_domain,
            "cloudflared_path": self.settings.cloudflared_path,
            "tunnels": {k: asdict(v) for k, v in self.settings.tunnels.items()},
            "ssh_profiles": [asdict(p) for p in self.settings.ssh_profiles],
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
