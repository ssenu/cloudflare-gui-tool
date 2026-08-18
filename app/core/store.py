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
    # 대상 기기가 재부팅돼도 systemd가 이 터널을 다시 띄우게 할지.
    # 켜면 상태 판정과 시작/정지도 systemd에 맡긴다(app/core/autostart.py).
    boot_autostart: bool = False


@dataclass
class ServerGroupMeta:
    """터널에 속하지 않는 서버 묶음(= 화면의 '서버 카테고리' 카드).

    안에 담는 항목은 RouteMeta를 그대로 쓴다 - 서버 하나를 표현하는 데 필요한
    것(label/service/server)이 이미 전부 있고, 다른 점은 hostname이 항상 빈
    문자열(= 도메인도 DNS 레코드도 없음)이라는 것뿐이다. 별도 타입을 만들면
    라우트 행과 서버 행이 공유하는 표시·실행 코드가 두 벌이 된다.
    """
    id: str                      # new_route_id()와 같은 8자리 hex
    name: str                    # 카드 제목 (예: "백엔드")
    servers: list[RouteMeta] = field(default_factory=list)


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
class RepoMeta:
    id: str            # new_route_id()와 같은 방식으로 생성 (secrets.token_hex(4))
    name: str          # 폴더 이름 (URL에서 자동 추출, 수정 가능)
    url: str
    path: str          # 대상 머신의 절대 경로 (예: /srv/apps/blog)
    branch: str = ""   # 비우면 기본 브랜치


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
    # 대상(로컬/SSH 프로필)별 클론된 저장소 목록. targets와 같은 이유로
    # 대상 키(CommandRunner.name)별로 분리한다 - 로컬과 원격 머신의 경로가
    # 서로 다르기 때문이다.
    repos: dict[str, list[RepoMeta]] = field(default_factory=dict)
    repo_root: str = "/srv/apps"
    # 터널을 만든(또는 자격증명을 확인해 옮겨온) 대상 키. tunnel list가
    # Cloudflare 계정 단위라 owner도 대상별이 아니라 계정 단위여야 한다 -
    # 그래서 targets처럼 대상 키로 나누지 않고, 터널 UUID(TunnelInfo.id, 이름
    # 변경에도 안전)를 키로 쓰는 단일 맵으로 둔다. 값은 CommandRunner.name
    # ("local" 또는 "ssh:<프로필명>").
    tunnel_owners: dict[str, str] = field(default_factory=dict)
    # 준비물 점검(cloudflared/git/docker)을 더 이상 보지 않기로 한 대상 키 목록.
    # 사용자가 "모두 정상"인 상태에서 직접 체크했을 때만 들어간다 - 그래서 새
    # 기기는 최초 1회 반드시 점검을 거친다.
    prereq_skip: list[str] = field(default_factory=list)
    # 대상(로컬/SSH 프로필)별 서버 카테고리 목록. repos와 같은 이유로 대상
    # 키로 나눈다 - 작업 폴더 경로도 컨테이너도 기기마다 다르다.
    server_groups: dict[str, list[ServerGroupMeta]] = field(default_factory=dict)
    # 기기 그룹(owner_key)별 카드 표시 순서. 항목 키는 터널이면 "t:<이름>",
    # 서버 카테고리면 "s:<그룹id>". 여기에 없는 카드는 뒤에 붙는다.
    card_order: dict[str, list[str]] = field(default_factory=dict)
    # 마지막 창 크기/위치 (x, y, w, h). 비어 있으면 기본값으로 연다.
    # 매번 같은 크기로 시작하면 넓게 놓고 쓰는 사람은 켤 때마다 다시 늘려야 한다.
    window_geometry: list[int] = field(default_factory=list)

    def skip_prereq_check(self, target_key: str) -> bool:
        return target_key in self.prereq_skip

    def set_skip_prereq_check(self, target_key: str, skip: bool) -> None:
        if skip and target_key not in self.prereq_skip:
            self.prereq_skip.append(target_key)
        elif not skip and target_key in self.prereq_skip:
            self.prereq_skip.remove(target_key)

    def tunnels_for(self, target_key: str) -> dict[str, TunnelMeta]:
        """target_key에 해당하는 터널 dict를 돌려준다. 없으면 새로 만들어 등록한다."""
        return self.targets.setdefault(target_key, {})

    def repos_for(self, target_key: str) -> list[RepoMeta]:
        """target_key에 해당하는 저장소 목록을 돌려준다. 없으면 새로 만들어 등록한다."""
        return self.repos.setdefault(target_key, [])

    def server_groups_for(self, target_key: str) -> list[ServerGroupMeta]:
        """target_key에 해당하는 서버 카테고리 목록. 없으면 새로 만들어 등록한다."""
        return self.server_groups.setdefault(target_key, [])


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

    boot = bool(v.get("boot_autostart", False))
    if "routes" in v:
        routes = _parse_routes_list(v.get("routes"))
        return TunnelMeta(name=name, routes=routes, boot_autostart=boot), False

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


def _parse_repo_meta(data) -> RepoMeta:
    """dict를 RepoMeta로 변환. id/name/url/path가 없거나 문자열이 아니면 TypeError."""
    kwargs = _filter_dataclass_kwargs(RepoMeta, data)
    for key in ("id", "name", "url", "path"):
        if not isinstance(kwargs.get(key), str) or not kwargs.get(key):
            raise TypeError(f"{key} must be non-empty str")
    return RepoMeta(**kwargs)


def _parse_repos_list(repos_data) -> list[RepoMeta]:
    """저장소 배열을 파싱. 잘못된 항목은 개별 스킵."""
    repos: list[RepoMeta] = []
    if isinstance(repos_data, list):
        for r in repos_data:
            try:
                repos.append(_parse_repo_meta(r))
            except (TypeError, ValueError):
                continue
    return repos


def _parse_server_group(data) -> ServerGroupMeta:
    """dict를 ServerGroupMeta로 변환. id/name이 비어 있지 않은 문자열이어야 한다."""
    kwargs = _filter_dataclass_kwargs(ServerGroupMeta, data)
    for key in ("id", "name"):
        if not isinstance(kwargs.get(key), str) or not kwargs.get(key):
            raise TypeError(f"{key} must be non-empty str")
    kwargs["servers"] = _parse_routes_list(kwargs.get("servers"))
    return ServerGroupMeta(**kwargs)


def _parse_server_groups_list(groups_data) -> list[ServerGroupMeta]:
    """서버 카테고리 배열을 파싱. 잘못된 항목은 개별 스킵."""
    groups: list[ServerGroupMeta] = []
    if isinstance(groups_data, list):
        for g in groups_data:
            try:
                groups.append(_parse_server_group(g))
            except (TypeError, ValueError):
                continue
    return groups


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

                # 유효한 SSH 프로필만 로드. 이름은 대상 키(ssh:<이름>)로 쓰이므로
                # 중복되면 같은 대상이 목록에 두 번 나오고 카드도 그만큼 겹쳐
                # 보인다. 먼저 나온 것만 남긴다.
                ssh_profiles = []
                seen_profile_names: set[str] = set()
                ssh_profiles_data = raw.get("ssh_profiles", [])
                if isinstance(ssh_profiles_data, list):
                    for p in ssh_profiles_data:
                        try:
                            profile_data = _filter_dataclass_kwargs(SshProfile, p)
                            profile = SshProfile(**profile_data)
                        except (TypeError, ValueError):
                            # 유효하지 않은 항목은 스킵
                            continue
                        if profile.name in seen_profile_names:
                            continue
                        seen_profile_names.add(profile.name)
                        ssh_profiles.append(profile)

                theme = raw.get("theme", "dark")
                if theme not in ("dark", "light"):
                    theme = "dark"

                # 대상별 저장소 목록 (키가 없던 기존 설정 파일도 문제없이 로드됨)
                repos: dict[str, list[RepoMeta]] = {}
                repos_data = raw.get("repos", {})
                if isinstance(repos_data, dict):
                    for target_key, repo_list in repos_data.items():
                        if not isinstance(target_key, str):
                            continue
                        repos[target_key] = _parse_repos_list(repo_list)

                # 대상별 서버 카테고리(터널 없는 서버 묶음). repos와 같은 형태.
                server_groups: dict[str, list[ServerGroupMeta]] = {}
                groups_data = raw.get("server_groups", {})
                if isinstance(groups_data, dict):
                    for target_key, group_list in groups_data.items():
                        if not isinstance(target_key, str):
                            continue
                        server_groups[target_key] = _parse_server_groups_list(group_list)

                # 카드 표시 순서. 문자열 키 -> 문자열 목록만 받아들이고,
                # 목록 안의 비문자열 항목은 개별로 걸러낸다.
                card_order: dict[str, list[str]] = {}
                order_data = raw.get("card_order", {})
                if isinstance(order_data, dict):
                    for owner_key, keys in order_data.items():
                        if not isinstance(owner_key, str) or not isinstance(keys, list):
                            continue
                        card_order[owner_key] = [k for k in keys if isinstance(k, str)]

                repo_root = raw.get("repo_root", "/srv/apps")
                if not isinstance(repo_root, str):
                    repo_root = "/srv/apps"

                # 터널 UUID -> 만든 대상 키. 계정 단위라 targets처럼 대상별로
                # 나누지 않는다. 키/값이 문자열이 아닌 항목은 개별 스킵(옛
                # 설정 파일에 아예 없던 필드라 없어도 정상 로드되어야 함).
                tunnel_owners: dict[str, str] = {}
                owners_data = raw.get("tunnel_owners", {})
                if isinstance(owners_data, dict):
                    for tid, owner in owners_data.items():
                        if isinstance(tid, str) and isinstance(owner, str):
                            tunnel_owners[tid] = owner

                # 점검 생략 목록: 문자열만 받아들인다(옛 파일에는 없는 필드).
                geo_raw = raw.get("window_geometry", [])
                window_geometry = (
                    [int(v) for v in geo_raw]
                    if isinstance(geo_raw, list) and len(geo_raw) == 4
                    and all(isinstance(v, (int, float)) for v in geo_raw)
                    else [])

                skip_raw = raw.get("prereq_skip", [])
                prereq_skip = ([k for k in skip_raw if isinstance(k, str)]
                               if isinstance(skip_raw, list) else [])

                self.settings = Settings(
                    root_domain=raw.get("root_domain", ""),
                    cloudflared_path=raw.get("cloudflared_path", ""),
                    targets=targets,
                    ssh_profiles=ssh_profiles,
                    theme=theme,
                    repos=repos,
                    repo_root=repo_root,
                    tunnel_owners=tunnel_owners,
                    prereq_skip=prereq_skip,
                    window_geometry=window_geometry,
                    server_groups=server_groups,
                    card_order=card_order,
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
        # dirname이 빈 문자열이면(파일명만 준 상대 경로) makedirs가 실패한다.
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        raw = {
            "root_domain": self.settings.root_domain,
            "cloudflared_path": self.settings.cloudflared_path,
            "targets": {
                target_key: {k: asdict(v) for k, v in tunnels.items()}
                for target_key, tunnels in self.settings.targets.items()
            },
            "ssh_profiles": [asdict(p) for p in self.settings.ssh_profiles],
            "theme": self.settings.theme,
            "repos": {
                target_key: [asdict(r) for r in repo_list]
                for target_key, repo_list in self.settings.repos.items()
            },
            "repo_root": self.settings.repo_root,
            "tunnel_owners": dict(self.settings.tunnel_owners),
            "prereq_skip": list(self.settings.prereq_skip),
            "window_geometry": list(self.settings.window_geometry),
            "server_groups": {
                target_key: [asdict(g) for g in group_list]
                for target_key, group_list in self.settings.server_groups.items()
            },
            "card_order": {
                owner_key: list(keys)
                for owner_key, keys in self.settings.card_order.items()
            },
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
