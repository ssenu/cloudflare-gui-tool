from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields, field


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
    theme: str = "dark"


def _filter_dataclass_kwargs(dataclass_type, data: dict) -> dict:
    """dataclass의 유효한 필드만 추출하고 나머지는 제거"""
    if not isinstance(data, dict):
        raise TypeError(f"Expected dict, got {type(data).__name__}")
    field_names = {f.name for f in fields(dataclass_type)}
    return {k: v for k, v in data.items() if k in field_names}


def default_settings_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "CloudflareTunnelGUI", "settings.json")


class SettingsStore:
    def __init__(self, path: str | None = None):
        self.path = path or default_settings_path()
        self.settings = Settings()

    def load(self) -> Settings:
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, IOError):
                # JSON 파싱 실패 또는 파일 읽기 실패 시 기본값으로 폴백
                return self.settings

            try:
                # 최상위가 dict인지 검증
                if not isinstance(raw, dict):
                    self.settings = Settings()
                    return self.settings

                # 유효한 터널만 로드
                tunnels = {}
                tunnels_data = raw.get("tunnels", {})
                if isinstance(tunnels_data, dict):
                    for k, v in tunnels_data.items():
                        try:
                            tunnel_data = _filter_dataclass_kwargs(TunnelMeta, v)
                            tunnels[k] = TunnelMeta(**tunnel_data)
                        except (TypeError, ValueError):
                            # 유효하지 않은 항목은 스킵
                            continue

                # 유효한 SSH 프로필만 로드
                ssh_profiles = []
                ssh_profiles_data = raw.get("ssh_profiles", [])
                if isinstance(ssh_profiles_data, list):
                    for p in ssh_profiles_data:
                        try:
                            profile_data = _filter_dataclass_kwargs(SshProfile, p)
                            ssh_profiles.append(SshProfile(**profile_data))
                        except (TypeError, ValueError):
                            # 유효하지 않은 항목은 스킵
                            continue

                theme = raw.get("theme", "dark")
                if theme not in ("dark", "light"):
                    theme = "dark"

                self.settings = Settings(
                    root_domain=raw.get("root_domain", ""),
                    cloudflared_path=raw.get("cloudflared_path", ""),
                    tunnels=tunnels,
                    ssh_profiles=ssh_profiles,
                    theme=theme,
                )
            except (TypeError, KeyError, ValueError, AttributeError):
                # 예상치 못한 형식 에러 시 기본값으로 폴백
                self.settings = Settings()

        return self.settings

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        raw = {
            "root_domain": self.settings.root_domain,
            "cloudflared_path": self.settings.cloudflared_path,
            "tunnels": {k: asdict(v) for k, v in self.settings.tunnels.items()},
            "ssh_profiles": [asdict(p) for p in self.settings.ssh_profiles],
            "theme": self.settings.theme,
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
