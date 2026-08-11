from __future__ import annotations

import yaml

FALLBACK_RULE = {"service": "http_status:404"}


def _ingress_from_routes(routes: list[tuple[str, str]]) -> list[dict]:
    """라우트 목록을 ingress 규칙 리스트로 변환하고 마지막에 fallback을 붙인다."""
    ingress = [{"hostname": hostname, "service": service}
               for hostname, service in routes]
    ingress.append(dict(FALLBACK_RULE))
    return ingress


def build_config(tunnel_id: str, credentials_file: str,
                 routes: list[tuple[str, str]]) -> str:
    data = {
        "tunnel": tunnel_id,
        "credentials-file": credentials_file,
        "ingress": _ingress_from_routes(routes),
    }
    return yaml.dump(data, sort_keys=False, allow_unicode=True)


def parse_config(text: str) -> dict:
    return yaml.safe_load(text) or {}


def get_routes(cfg: dict) -> list[tuple[str, str]]:
    """ingress 규칙에서 hostname이 있는 라우트만 (hostname, service) 순서대로 반환한다."""
    ingress = cfg.get("ingress") or []
    if not isinstance(ingress, list):
        return []
    routes = []
    for rule in ingress:
        if isinstance(rule, dict) and "hostname" in rule:
            routes.append((rule.get("hostname", ""), rule.get("service", "")))
    return routes


def set_routes(text: str, routes: list[tuple[str, str]]) -> str:
    """tunnel/credentials-file 등 다른 최상위 키는 보존한 채 ingress만 교체한다."""
    cfg = parse_config(text)
    cfg["ingress"] = _ingress_from_routes(routes)
    return yaml.dump(cfg, sort_keys=False, allow_unicode=True)


# ---- 이전 단일 라우트 API (다른 모듈이 아직 사용 중이므로 새 API 위의 얇은 래퍼로 유지) ----

def get_main_ingress(cfg: dict) -> tuple[str, str]:
    routes = get_routes(cfg)
    return routes[0] if routes else ("", "")


def update_service(text: str, service: str) -> str:
    cfg = parse_config(text)
    routes = get_routes(cfg)
    if routes:
        routes[0] = (routes[0][0], service)
    return set_routes(text, routes)
