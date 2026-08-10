from app.core.config_yml import (build_config, get_main_ingress,
                                 parse_config, update_service)

TID = "a1b2c3d4-1111-2222-3333-444455556666"
CRED = "C:/Users/me/.cloudflared/a1b2c3d4.json"


def test_build_and_parse_roundtrip():
    text = build_config(TID, CRED, "mysite.example.com", "http://localhost:8000")
    cfg = parse_config(text)
    assert cfg["tunnel"] == TID
    assert cfg["credentials-file"] == CRED
    assert cfg["ingress"][0] == {"hostname": "mysite.example.com",
                                 "service": "http://localhost:8000"}
    assert cfg["ingress"][-1] == {"service": "http_status:404"}  # fallback 필수


def test_get_main_ingress():
    text = build_config(TID, CRED, "a.example.com", "http://localhost:9000")
    assert get_main_ingress(parse_config(text)) == ("a.example.com",
                                                    "http://localhost:9000")
    assert get_main_ingress({}) == ("", "")


def test_update_service_keeps_rest():
    text = build_config(TID, CRED, "a.example.com", "http://localhost:9000")
    new = update_service(text, "http://localhost:5173")
    cfg = parse_config(new)
    assert cfg["ingress"][0]["service"] == "http://localhost:5173"
    assert cfg["ingress"][0]["hostname"] == "a.example.com"
    assert cfg["tunnel"] == TID


def test_non_dict_ingress_entries_ignored():
    text = f"""
tunnel: {TID}
credentials-file: {CRED}
ingress:
  - "not-a-dict-entry"
  - hostname: a.example.com
    service: http://localhost:9000
  - service: http_status:404
"""
    cfg = parse_config(text)
    assert get_main_ingress(cfg) == ("a.example.com", "http://localhost:9000")
    new = update_service(text, "http://localhost:5173")
    new_cfg = parse_config(new)
    assert get_main_ingress(new_cfg) == ("a.example.com", "http://localhost:5173")
