from __future__ import annotations

import yaml


def build_config(tunnel_id: str, credentials_file: str,
                 hostname: str, service: str) -> str:
    data = {
        "tunnel": tunnel_id,
        "credentials-file": credentials_file,
        "ingress": [
            {"hostname": hostname, "service": service},
            {"service": "http_status:404"},
        ],
    }
    return yaml.dump(data, sort_keys=False, allow_unicode=True)


def parse_config(text: str) -> dict:
    return yaml.safe_load(text) or {}


def get_main_ingress(cfg: dict) -> tuple[str, str]:
    for rule in cfg.get("ingress", []):
        if "hostname" in rule:
            return rule.get("hostname", ""), rule.get("service", "")
    return "", ""


def update_service(text: str, service: str) -> str:
    cfg = parse_config(text)
    for rule in cfg.get("ingress", []):
        if "hostname" in rule:
            rule["service"] = service
            break
    return yaml.dump(cfg, sort_keys=False, allow_unicode=True)
