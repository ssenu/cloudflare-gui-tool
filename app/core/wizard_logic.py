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
