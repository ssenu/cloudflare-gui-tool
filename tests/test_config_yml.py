from app.core.config_yml import (build_config, get_main_ingress, get_routes,
                                 parse_config, set_routes, update_service)

TID = "a1b2c3d4-1111-2222-3333-444455556666"
CRED = "C:/Users/me/.cloudflared/a1b2c3d4.json"

ROUTES = [
    ("a.example.com", "http://localhost:8000"),
    ("b.example.com", "http://localhost:8001"),
    ("c.example.com", "http://localhost:8002"),
]


def test_build_and_get_routes_roundtrip():
    text = build_config(TID, CRED, ROUTES)
    cfg = parse_config(text)
    assert cfg["tunnel"] == TID
    assert cfg["credentials-file"] == CRED
    assert get_routes(cfg) == ROUTES


def test_ingress_order_preserved():
    text = build_config(TID, CRED, ROUTES)
    cfg = parse_config(text)
    ingress = cfg["ingress"]
    assert [rule["hostname"] for rule in ingress[:-1]] == [h for h, _ in ROUTES]


def test_fallback_always_last_and_single():
    text = build_config(TID, CRED, ROUTES)
    cfg = parse_config(text)
    ingress = cfg["ingress"]
    assert ingress[-1] == {"service": "http_status:404"}
    fallback_count = sum(1 for rule in ingress if rule == {"service": "http_status:404"})
    assert fallback_count == 1


def test_empty_routes_leaves_only_fallback():
    text = build_config(TID, CRED, [])
    cfg = parse_config(text)
    assert cfg["ingress"] == [{"service": "http_status:404"}]


def test_set_routes_preserves_tunnel_and_credentials():
    text = build_config(TID, CRED, [ROUTES[0]])
    new_routes = [ROUTES[1], ROUTES[2]]
    new_text = set_routes(text, new_routes)
    cfg = parse_config(new_text)
    assert cfg["tunnel"] == TID
    assert cfg["credentials-file"] == CRED
    assert get_routes(cfg) == new_routes
    assert cfg["ingress"][-1] == {"service": "http_status:404"}


def test_set_routes_empty_leaves_fallback_only():
    text = build_config(TID, CRED, ROUTES)
    new_text = set_routes(text, [])
    cfg = parse_config(new_text)
    assert cfg["ingress"] == [{"service": "http_status:404"}]


def test_get_routes_ignores_non_dict_and_fallback_entries():
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
    assert get_routes(cfg) == [("a.example.com", "http://localhost:9000")]


def test_get_routes_ingress_not_list_returns_empty():
    assert get_routes({"ingress": "not-a-list"}) == []
    assert get_routes({}) == []


# ---- 이전 API가 새 API 위의 얇은 래퍼로 유지되는지 (다른 모듈이 계속 사용) ----

def test_get_main_ingress_wraps_get_routes():
    text = build_config(TID, CRED, [("a.example.com", "http://localhost:9000")])
    assert get_main_ingress(parse_config(text)) == ("a.example.com",
                                                    "http://localhost:9000")
    assert get_main_ingress({}) == ("", "")


def test_update_service_keeps_rest():
    text = build_config(TID, CRED, [("a.example.com", "http://localhost:9000")])
    new = update_service(text, "http://localhost:5173")
    cfg = parse_config(new)
    assert cfg["ingress"][0]["service"] == "http://localhost:5173"
    assert cfg["ingress"][0]["hostname"] == "a.example.com"
    assert cfg["tunnel"] == TID
