from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, fields, field


@dataclass
class ServiceSpec:
    kind: str = "command"        # "command" | "docker"
    start_cmd: str = ""
    stop_cmd: str = ""           # 비우면 PID 종료로 처리 (command 전용)
    cwd: str = ""
    autostart: bool = False      # 터널을 켤 때 함께 시작


@dataclass
class RouteMeta:
    id: str                      # 8자리 난수 hex, 생성 후 불변
    hostname: str = ""           # mysite.example.com
    service: str = ""            # http://localhost:8000
    server: ServiceSpec = field(default_factory=ServiceSpec)
    label: str = ""              # 사용자가 붙인 이름(선택). 비우면 hostname 첫 라벨을 대신 표시


@dataclass
class TunnelMeta:
    name: str
    routes: list[RouteMeta] = field(default_factory=list)


def new_route_id() -> str:
    """라우트의 안정적인 식별자를 생성한다 (hostname이 바뀌어도 PID/로그와의 연결 유지)"""
    return secrets.token_hex(4)


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
    # 대상(로컬/SSH 프로필)별로 독립된 터널 설정. 바깥 키는
    # CommandRunner.name ("local" 또는 "ssh:<프로필명>"), 안쪽은 기존과 같은
    # {터널이름: TunnelMeta} dict. 같은 계정을 여러 대상에서 볼 수 있지만
    # config-*.yml/프로세스/서버 실행 명령은 대상마다 다르므로 분리해야 한다.
    targets: dict[str, dict[str, TunnelMeta]] = field(default_factory=dict)
    ssh_profiles: list[SshProfile] = field(default_factory=list)
    theme: str = "dark"

    def tunnels_for(self, target_key: str) -> dict[str, TunnelMeta]:
        """target_key에 해당하는 터널 dict를 돌려준다. 없으면 새로 만들어 등록한다."""
        return self.targets.setdefault(target_key, {})


def _filter_dataclass_kwargs(dataclass_type, data: dict) -> dict:
    """dataclass의 유효한 필드만 추출하고 나머지는 제거"""
    if not isinstance(data, dict):
        raise TypeError(f"Expected dict, got {type(data).__name__}")
    field_names = {f.name for f in fields(dataclass_type)}
    return {k: v for k, v in data.items() if k in field_names}


def _parse_service_spec(data) -> ServiceSpec:
    """dict를 ServiceSpec으로 변환. kind가 유효하지 않으면 'command'로 보정"""
    kwargs = _filter_dataclass_kwargs(ServiceSpec, data)
    spec = ServiceSpec(**kwargs)
    if spec.kind not in ("command", "docker"):
        spec.kind = "command"
    return spec


def _parse_route_meta(data) -> RouteMeta:
    """dict를 RouteMeta로 변환. id가 없거나 문자열이 아니면 TypeError (유효하지 않은 항목)"""
    kwargs = _filter_dataclass_kwargs(RouteMeta, data)
    if not isinstance(kwargs.get("id"), str):
        raise TypeError("id must be str")
    # server가 dict가 아니면(None 포함, 잘못된 타입 포함) 기본 ServiceSpec으로 대체.
    # None만 통과시키고 다른 잘못된 타입은 거부하는 비대칭을 두지 않는다.
    server_raw = kwargs.get("server")
    if isinstance(server_raw, dict):
        kwargs["server"] = _parse_service_spec(server_raw)
    else:
        kwargs.pop("server", None)
    return RouteMeta(**kwargs)


def _parse_routes_list(routes_data) -> list[RouteMeta]:
    """라우트 배열을 파싱. 잘못된 항목은 개별 스킵"""
    routes: list[RouteMeta] = []
    if isinstance(routes_data, list):
        for r in routes_data:
            try:
                routes.append(_parse_route_meta(r))
            except (TypeError, ValueError):
                continue
    return routes


def _parse_tunnel_meta(v) -> tuple[TunnelMeta, bool]:
    """v1/v2 터널 dict를 TunnelMeta로 변환.

    반환값은 (meta, migrated) - migrated는 v1 형식에서 실제로
    라우트가 생성된 경우에만 True.
    """
    if not isinstance(v, dict):
        raise TypeError(f"Expected dict, got {type(v).__name__}")

    name = v["name"]  # 없으면 KeyError -> 호출자가 무효 항목으로 스킵
    if not isinstance(name, str):
        raise TypeError("name must be str")

    if "routes" in v:
        routes = _parse_routes_list(v.get("routes"))
        return TunnelMeta(name=name, routes=routes), False

    # v1 형식 마이그레이션
    hostname = v.get("hostname", "") or ""
    service = v.get("service", "") or ""
    server_cmd = v.get("server_cmd", "") or ""
    server_cwd = v.get("server_cwd", "") or ""
    start_together = bool(v.get("start_together", False))

    if hostname or service or server_cmd:
        route = RouteMeta(
            id=new_route_id(),
            hostname=hostname,
            service=service,
            server=ServiceSpec(
                kind="command",
                start_cmd=server_cmd,
                cwd=server_cwd,
                autostart=start_together,
            ),
        )
        return TunnelMeta(name=name, routes=[route]), True

    return TunnelMeta(name=name, routes=[]), False


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

                # 유효한 터널만 로드 (v1 -> v2 라우트 마이그레이션 포함,
                # 평평한 tunnels -> targets["local"] 구조 마이그레이션 포함)
                migrated_any = False

                def _parse_tunnels_dict(tunnels_data) -> dict:
                    nonlocal migrated_any
                    result = {}
                    if isinstance(tunnels_data, dict):
                        for k, v in tunnels_data.items():
                            try:
                                meta, migrated = _parse_tunnel_meta(v)
                                result[k] = meta
                                migrated_any = migrated_any or migrated
                            except (TypeError, ValueError, KeyError):
                                # 유효하지 않은 항목은 스킵
                                continue
                    return result

                targets: dict[str, dict] = {}
                if "targets" in raw:
                    targets_data = raw.get("targets", {})
                    if isinstance(targets_data, dict):
                        for target_key, tunnels_data in targets_data.items():
                            if not isinstance(target_key, str):
                                continue
                            targets[target_key] = _parse_tunnels_dict(tunnels_data)
                elif "tunnels" in raw:
                    # v1/v2 평평한 형식: 기존 tunnels는 전부 로컬 대상 것이었다
                    targets["local"] = _parse_tunnels_dict(raw.get("tunnels", {}))
                    migrated_any = True

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
                    targets=targets,
                    ssh_profiles=ssh_profiles,
                    theme=theme,
                )

                # v1 형식에서 실제로 마이그레이션이 일어난 경우에만 재저장.
                # v2 형식만 있는 파일은 매번 다시 쓰지 않는다.
                if migrated_any:
                    try:
                        self.save()
                    except OSError:
                        # 디스크 가득/읽기 전용/클라우드 동기화 잠금 등으로 저장에
                        # 실패해도 메모리상의 마이그레이션 결과는 그대로 유지한 채
                        # 조용히 진행한다 (다음 save() 시점에 다시 시도됨).
                        pass
            except (TypeError, KeyError, ValueError, AttributeError):
                # 예상치 못한 형식 에러 시 기본값으로 폴백
                self.settings = Settings()

        return self.settings

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        raw = {
            "root_domain": self.settings.root_domain,
            "cloudflared_path": self.settings.cloudflared_path,
            "targets": {
                target_key: {k: asdict(v) for k, v in tunnels.items()}
                for target_key, tunnels in self.settings.targets.items()
            },
            "ssh_profiles": [asdict(p) for p in self.settings.ssh_profiles],
            "theme": self.settings.theme,
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
