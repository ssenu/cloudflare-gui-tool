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
                     progress: Callable[[int, str, bool], None]) -> None:
    progress(0, "터널 생성 중...", True)
    tid, cred = client.create_tunnel(name)
    progress(0, f"터널 생성 완료 (id: {tid[:8]}...)", True)

    progress(1, "DNS 라우팅 중...", True)
    client.route_dns(name, hostname)
    progress(1, f"{hostname} 연결 완료", True)

    progress(2, "설정 파일 작성 중...", True)
    text = build_config(tid, cred, [(hostname, service)])
    client.runner.write_file(client.config_path(name), text)
    progress(2, "설정 파일 작성 완료", True)
