from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from app.core.cloudflared import CloudflaredClient
from app.core.config_yml import build_config

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SUB_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$")
SERVICE_RE = re.compile(r"^(https?|tcp|ssh|rdp)://\S+$")


def validate_name(name: str, existing: list[str]) -> str:
    if not name:
        return "터널 이름을 입력하세요"
    if not NAME_RE.match(name):
        return "영문, 숫자, -, _ 만 사용할 수 있습니다"
    if name in existing:
        return "같은 이름의 터널이 이미 존재합니다"
    return ""


def validate_subdomain(sub: str) -> str:
    if not sub:
        return "서브도메인을 입력하세요"
    if not SUB_RE.match(sub):
        return "영문, 숫자, - 만 사용할 수 있습니다"
    return ""


def validate_service(url: str) -> str:
    if not url:
        return "서비스 주소를 입력하세요"
    if not SERVICE_RE.match(url):
        return "http:// 또는 https:// 로 시작하는 주소여야 합니다 (예: http://localhost:8000)"
    return ""


@dataclass
class PlannedStep:
    label: str
    preview: str


def plan_steps(name: str, hostname: str, service: str) -> list[PlannedStep]:
    return [
        PlannedStep("터널 생성", f"cloudflared tunnel create {name}"),
        PlannedStep("DNS 연결", f"cloudflared tunnel route dns {name} {hostname}"),
        PlannedStep("설정 파일 작성",
                    f"~/.cloudflared/config-{name}.yml 생성 ({hostname} → {service})"),
    ]


class ConfigRecoveryError(Exception):
    """설정 파일을 새로 만들 수 없는 상황(터널을 못 찾음/자격증명 없음)."""


def ensure_config(client: CloudflaredClient, name: str) -> bool:
    """config-<name>.yml이 없으면 라우트 없는 기본 설정으로 만든다.

    만들었으면 True, 이미 있으면 False.

    설정 파일이 없는 터널은 여러 경로로 생긴다 - 도메인 없이 터널만 만든 뒤
    다른 기기에서 열었을 때, 터미널에서 직접 `cloudflared tunnel create`로
    만들었을 때, 설정 파일만 지웠을 때. 이런 터널에 라우트를 붙이려는 것은
    지극히 정상적인 요구라서, 없다고 거절하는 대신 그 자리에서 만들어 준다.

    다만 다음 두 경우에는 만들지 않고 ConfigRecoveryError를 던진다. 잘못된
    파일을 써 두면 터널이 켜지는 것처럼 보이다가 실행 시점에 실패한다:
    - 계정의 터널 목록에 그 이름이 없다 (id를 알 수 없다)
    - 이 대상에 자격증명 파일이 없다 (있어도 이 기기에서는 실행 불가)
    """
    path = client.config_path(name)
    if client.runner.file_exists(path):
        return False

    tunnel_id = ""
    for info in client.list_tunnels():
        if info.name == name:
            tunnel_id = info.id
            break
    if not tunnel_id:
        raise ConfigRecoveryError(
            f"'{name}' 터널을 Cloudflare 계정에서 찾지 못했습니다. "
            "목록을 새로고침한 뒤 다시 시도하세요.")

    credentials = client.credentials_path(tunnel_id)
    if not client.runner.file_exists(credentials):
        raise ConfigRecoveryError(
            f"이 기기에 '{name}' 터널의 자격증명 파일이 없어 설정을 만들 수 "
            f"없습니다.\n{credentials}\n\n"
            "터널을 만든 기기에서 라우트를 추가하거나, 그 기기의 자격증명 "
            "파일을 이 경로로 복사하세요.")

    client.runner.write_file(path, build_config(tunnel_id, credentials, []))
    return True


def plan_steps_tunnel_only(name: str) -> list[PlannedStep]:
    """도메인 연결 없이 터널만 만들 때의 단계."""
    return [
        PlannedStep("터널 생성", f"cloudflared tunnel create {name}"),
        PlannedStep("설정 파일 작성",
                    f"~/.cloudflared/config-{name}.yml 생성 (라우트 없음)"),
    ]


def create_tunnel_only(client: CloudflaredClient, name: str,
                       progress: Callable[[int, str, bool], None],
                       created: dict | None = None) -> None:
    """터널만 만들고 설정 파일은 라우트 없이 쓴다(DNS 연결 없음).

    라우트가 하나도 없어도 cloudflared는 정상적으로 실행된다 - ingress에
    404 폴백만 남기 때문이다. 도메인은 나중에 "라우트 추가"로 붙인다.
    """
    progress(0, "터널 생성 중...", True)
    tid, cred = client.create_tunnel(name)
    if created is not None:
        created["tunnel_id"] = tid
        created["credentials"] = cred
    progress(0, f"터널 생성 완료 (id: {tid[:8]}...)", True)

    progress(1, "설정 파일 작성 중...", True)
    text = build_config(tid, cred, [])
    client.runner.write_file(client.config_path(name), text)
    progress(1, "설정 파일 작성 완료 (라우트는 나중에 추가)", True)


def execute_creation(client: CloudflaredClient, name: str, hostname: str,
                     service: str,
                     progress: Callable[[int, str, bool], None],
                     created: dict | None = None) -> None:
    """터널 생성 → DNS 연결 → 설정 파일 작성을 순서대로 수행한다.

    ``created``가 주어지면 create_tunnel() 성공 직후 그 결과
    (``tunnel_id``/``credentials``)를 채워 넣는다 - 이후 단계(DNS 연결)가
    실패해도 호출자가 이미 만들어진 터널 정보를 알 수 있게 하기 위함이다
    (마법사의 "기존 DNS 레코드 덮어쓰기" 재개 경로가 이 값을 쓴다).
    """
    progress(0, "터널 생성 중...", True)
    tid, cred = client.create_tunnel(name)
    if created is not None:
        created["tunnel_id"] = tid
        created["credentials"] = cred
    progress(0, f"터널 생성 완료 (id: {tid[:8]}...)", True)

    finish_creation(client, name, hostname, service, tid, cred, progress)


def finish_creation(client: CloudflaredClient, name: str, hostname: str,
                    service: str, tunnel_id: str, credentials: str,
                    progress: Callable[[int, str, bool], None],
                    overwrite_dns: bool = False) -> None:
    """DNS 연결 + 설정 파일 작성만 수행한다 (터널 생성은 이미 끝난 상태).

    DNS 레코드 충돌(DnsRecordExistsError)로 실패한 뒤, 이미 만들어진 터널을
    다시 만들지 않고 DNS 연결부터 재개할 때 쓴다.
    """
    progress(1, "DNS 라우팅 중...", True)
    client.route_dns(name, hostname, overwrite=overwrite_dns)
    progress(1, f"{hostname} 연결 완료", True)

    progress(2, "설정 파일 작성 중...", True)
    text = build_config(tunnel_id, credentials, [(hostname, service)])
    client.runner.write_file(client.config_path(name), text)
    progress(2, "설정 파일 작성 완료", True)
