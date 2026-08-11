import json
import os
import time

from app.core.store import (RouteMeta, ServiceSpec, SettingsStore, SshProfile,
                            TunnelMeta, new_route_id)


def test_load_missing_file_returns_defaults(tmp_path):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    s = store.load()
    assert s.root_domain == ""
    assert s.tunnels == {}
    assert s.ssh_profiles == []


def test_save_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()
    store.settings.root_domain = "example.com"
    store.settings.tunnels["mysite"] = TunnelMeta(
        name="mysite",
        routes=[
            RouteMeta(
                id="abcd1234",
                hostname="mysite.example.com",
                service="http://localhost:8000",
                server=ServiceSpec(
                    kind="command",
                    start_cmd="uvicorn main:app --port 8000",
                    cwd="C:/proj",
                    autostart=True,
                ),
            )
        ],
    )
    store.settings.ssh_profiles.append(
        SshProfile(name="rpi", host="192.168.0.10", port=22,
                   username="pi", key_path="C:/keys/id_ed25519"))
    store.save()

    loaded = SettingsStore(path=path).load()
    assert loaded.root_domain == "example.com"
    assert loaded.tunnels["mysite"].routes[0].server.autostart is True
    assert loaded.ssh_profiles[0].host == "192.168.0.10"
    # 파일이 사람이 읽을 수 있는 JSON인지
    raw = json.loads(open(path, encoding="utf-8").read())
    assert "tunnels" in raw


def test_theme_roundtrip(tmp_path):
    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()
    assert store.settings.theme == "dark"
    store.settings.theme = "light"
    store.save()

    loaded = SettingsStore(path=path).load()
    assert loaded.theme == "light"


def test_load_corrupt_json_falls_back_to_defaults(tmp_path):
    """손상된 JSON은 기본값으로 폴백되어야 함"""
    path = str(tmp_path / "settings.json")
    # 손상된 JSON 작성 (미완성)
    with open(path, "w", encoding="utf-8") as f:
        f.write("not json{{{")

    store = SettingsStore(path=path)
    # load()가 예외를 raise하지 않고 기본값을 반환해야 함
    s = store.load()
    assert s.root_domain == ""
    assert s.tunnels == {}
    assert s.ssh_profiles == []


def test_load_ignores_bad_entries(tmp_path):
    """형식이 잘못된 항목은 무시되고 유효한 항목은 로드됨"""
    path = str(tmp_path / "settings.json")
    bad_json = {
        "root_domain": "example.com",
        "cloudflared_path": "/path/to/cloudflared",
        "tunnels": {
            "good": {
                "name": "good",
                "routes": [
                    {
                        "id": "aaaa1111",
                        "hostname": "good.example.com",
                        "service": "http://localhost:8000",
                        "server": {"kind": "command"},
                    }
                ],
            },
            "missing_name": {
                # name이 없음 - 유효하지 않음
                "routes": []
            },
            "extra_field": {
                "name": "extra",
                "routes": [],
                "unknown_field": "should_be_filtered"
                # dataclass에 없는 필드는 필터링되어야 함
            }
        },
        "ssh_profiles": [
            {
                "name": "good_ssh",
                "host": "192.168.0.10",
                "port": 22,
                "username": "pi",
                "key_path": ""
            },
            "invalid_string_entry"  # 잘못된 타입
        ]
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(bad_json, f)

    store = SettingsStore(path=path)
    s = store.load()

    # 기본값인 root_domain은 로드됨
    assert s.root_domain == "example.com"
    # 유효한 터널만 로드됨 (good, extra_field는 유효함 / missing_name은 필수 필드 없음)
    assert "good" in s.tunnels
    assert "extra_field" in s.tunnels
    assert "missing_name" not in s.tunnels
    assert len(s.tunnels) == 2  # 유효하지 않은 항목 1개는 제외
    # 유효한 SSH 프로필만 로드됨
    assert len(s.ssh_profiles) == 1
    assert s.ssh_profiles[0].name == "good_ssh"


def test_load_non_object_root_falls_back(tmp_path):
    """최상위 JSON이 객체가 아니면 기본값으로 폴백"""
    path = str(tmp_path / "settings.json")
    # 배열 JSON (객체가 아님)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([1, 2, 3], f)

    store = SettingsStore(path=path)
    # AttributeError가 발생하지 않고 기본값을 반환해야 함
    s = store.load()
    assert s.root_domain == ""
    assert s.tunnels == {}
    assert s.ssh_profiles == []


def test_load_tunnels_wrong_type_skipped(tmp_path):
    """tunnels이 dict가 아니면 빈 dict로 처리"""
    path = str(tmp_path / "settings.json")
    bad_json = {
        "root_domain": "example.com",
        "cloudflared_path": "/path/to/cloudflared",
        "tunnels": ["a", "b"]  # dict가 아닌 list
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(bad_json, f)

    store = SettingsStore(path=path)
    # AttributeError가 발생하지 않고 터널을 빈 dict로 처리
    s = store.load()
    assert s.root_domain == "example.com"
    assert s.tunnels == {}  # 형식이 잘못되었으므로 빈 dict
    assert s.ssh_profiles == []


# ---- v2 데이터 모델 / 마이그레이션 ----

def test_new_route_id_format():
    rid = new_route_id()
    assert isinstance(rid, str)
    assert len(rid) == 8  # secrets.token_hex(4) -> 8 hex chars
    int(rid, 16)  # 유효한 hex 문자열이어야 함


def test_v1_format_migrates_to_single_route_and_resaves(tmp_path):
    """v1 형식(hostname/service/server_cmd 최상위)을 로드하면 라우트 1개로 변환되고,
    autostart가 보존되며, 파일이 새 형식으로 다시 저장된다."""
    path = str(tmp_path / "settings.json")
    v1_json = {
        "root_domain": "example.com",
        "cloudflared_path": "",
        "tunnels": {
            "mysite": {
                "name": "mysite",
                "hostname": "mysite.example.com",
                "service": "http://localhost:8000",
                "server_cmd": "uvicorn main:app --port 8000",
                "server_cwd": "C:/proj",
                "start_together": True,
            }
        },
        "ssh_profiles": [],
        "theme": "dark",
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(v1_json, f)

    store = SettingsStore(path=path)
    s = store.load()

    meta = s.tunnels["mysite"]
    assert len(meta.routes) == 1
    route = meta.routes[0]
    assert route.hostname == "mysite.example.com"
    assert route.service == "http://localhost:8000"
    assert route.server.kind == "command"
    assert route.server.start_cmd == "uvicorn main:app --port 8000"
    assert route.server.cwd == "C:/proj"
    assert route.server.autostart is True
    assert isinstance(route.id, str) and route.id

    # 파일이 v2 형식(routes 키)으로 다시 저장되었는지 확인
    raw = json.loads(open(path, encoding="utf-8").read())
    assert "routes" in raw["tunnels"]["mysite"]
    assert "hostname" not in raw["tunnels"]["mysite"]


def test_v1_all_fields_empty_yields_empty_routes(tmp_path):
    """v1 필드가 전부 비어있으면 빈 routes로 변환된다."""
    path = str(tmp_path / "settings.json")
    v1_json = {
        "root_domain": "",
        "cloudflared_path": "",
        "tunnels": {
            "empty": {
                "name": "empty",
                "hostname": "",
                "service": "",
                "server_cmd": "",
                "server_cwd": "",
                "start_together": False,
            }
        },
        "ssh_profiles": [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(v1_json, f)

    store = SettingsStore(path=path)
    s = store.load()
    assert s.tunnels["empty"].routes == []


def test_v2_roundtrip_multiple_routes_with_docker_service(tmp_path):
    """v2 형식 왕복: 라우트 2개, 그중 1개는 도커 서비스."""
    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()
    store.settings.tunnels["multi"] = TunnelMeta(
        name="multi",
        routes=[
            RouteMeta(
                id=new_route_id(),
                hostname="a.example.com",
                service="http://localhost:8000",
                server=ServiceSpec(kind="command", start_cmd="run.exe", autostart=False),
            ),
            RouteMeta(
                id=new_route_id(),
                hostname="b.example.com",
                service="http://localhost:9000",
                server=ServiceSpec(kind="docker", start_cmd="docker compose up -d",
                                   stop_cmd="docker compose down", autostart=True),
            ),
        ],
    )
    store.save()

    loaded = SettingsStore(path=path).load()
    meta = loaded.tunnels["multi"]
    assert len(meta.routes) == 2
    assert meta.routes[0].server.kind == "command"
    assert meta.routes[1].server.kind == "docker"
    assert meta.routes[1].server.stop_cmd == "docker compose down"
    assert meta.routes[1].server.autostart is True


def test_route_array_with_bad_entries_loads_the_rest(tmp_path):
    """라우트 배열에 잘못된 항목이 섞여 있어도 나머지는 로드된다."""
    path = str(tmp_path / "settings.json")
    v2_json = {
        "root_domain": "",
        "cloudflared_path": "",
        "tunnels": {
            "mixed": {
                "name": "mixed",
                "routes": [
                    {"id": "aaaa1111", "hostname": "good.example.com",
                     "service": "http://localhost:8000"},
                    {"hostname": "no-id.example.com"},  # id 없음 -> 유효하지 않음
                    "not_a_dict",  # 잘못된 타입
                    {"id": "bbbb2222", "hostname": "good2.example.com"},
                ],
            }
        },
        "ssh_profiles": [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(v2_json, f)

    store = SettingsStore(path=path)
    s = store.load()
    routes = s.tunnels["mixed"].routes
    assert len(routes) == 2
    assert {r.id for r in routes} == {"aaaa1111", "bbbb2222"}


def test_service_spec_bad_kind_coerced_to_command(tmp_path):
    """server.kind 값이 이상하면 'command'로 보정된다."""
    path = str(tmp_path / "settings.json")
    v2_json = {
        "root_domain": "",
        "cloudflared_path": "",
        "tunnels": {
            "t": {
                "name": "t",
                "routes": [
                    {"id": "aaaa1111", "hostname": "h.example.com",
                     "server": {"kind": "not_a_real_kind"}},
                ],
            }
        },
        "ssh_profiles": [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(v2_json, f)

    store = SettingsStore(path=path)
    s = store.load()
    assert s.tunnels["t"].routes[0].server.kind == "command"


def test_v2_only_file_load_does_not_rewrite_file(tmp_path):
    """v2 형식만 있는 파일은 load()가 파일을 다시 저장하지 않는다."""
    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()
    store.settings.tunnels["t"] = TunnelMeta(
        name="t",
        routes=[RouteMeta(id=new_route_id(), hostname="h.example.com",
                          service="http://localhost:8000")],
    )
    store.save()

    mtime_before = os.path.getmtime(path)
    time.sleep(0.05)

    store2 = SettingsStore(path=path)
    save_calls = []
    store2.save = lambda: save_calls.append(True)
    store2.load()

    assert save_calls == []
    assert os.path.getmtime(path) == mtime_before


def test_route_server_null_falls_back_to_default_service_spec(tmp_path):
    """server가 null이어도 기본 ServiceSpec으로 로드되어야 한다 (거부와 비대칭 금지)."""
    path = str(tmp_path / "settings.json")
    v2_json = {
        "root_domain": "",
        "cloudflared_path": "",
        "tunnels": {
            "t": {
                "name": "t",
                "routes": [
                    {"id": "aaaa1111", "hostname": "h.example.com", "server": None},
                ],
            }
        },
        "ssh_profiles": [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(v2_json, f)

    store = SettingsStore(path=path)
    s = store.load()
    route = s.tunnels["t"].routes[0]
    assert route.server == ServiceSpec()
    assert route.server.kind == "command"


def test_route_server_wrong_type_falls_back_to_default_service_spec(tmp_path):
    """server가 dict가 아닌 잘못된 타입(예: 숫자)이어도 라우트 자체는 로드되고
    기본 ServiceSpec으로 대체된다."""
    path = str(tmp_path / "settings.json")
    v2_json = {
        "root_domain": "",
        "cloudflared_path": "",
        "tunnels": {
            "t": {
                "name": "t",
                "routes": [
                    {"id": "aaaa1111", "hostname": "h.example.com", "server": 123},
                ],
            }
        },
        "ssh_profiles": [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(v2_json, f)

    store = SettingsStore(path=path)
    s = store.load()
    route = s.tunnels["t"].routes[0]
    assert route.server == ServiceSpec()
    assert route.server.kind == "command"


def test_route_id_wrong_type_is_skipped(tmp_path):
    """id가 문자열이 아니면 name 검사와 동일한 수준으로 해당 라우트를 스킵한다."""
    path = str(tmp_path / "settings.json")
    v2_json = {
        "root_domain": "",
        "cloudflared_path": "",
        "tunnels": {
            "t": {
                "name": "t",
                "routes": [
                    {"id": 12345, "hostname": "bad.example.com"},
                    {"id": "bbbb2222", "hostname": "good.example.com"},
                ],
            }
        },
        "ssh_profiles": [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(v2_json, f)

    store = SettingsStore(path=path)
    s = store.load()
    routes = s.tunnels["t"].routes
    assert len(routes) == 1
    assert routes[0].id == "bbbb2222"


def test_migration_save_oserror_does_not_crash_load(tmp_path, monkeypatch):
    """마이그레이션 재저장이 OSError로 실패해도 load()는 예외 없이 마이그레이션된
    Settings를 메모리상에 반환해야 한다."""
    path = str(tmp_path / "settings.json")
    v1_json = {
        "root_domain": "example.com",
        "cloudflared_path": "",
        "tunnels": {
            "mysite": {
                "name": "mysite",
                "hostname": "mysite.example.com",
                "service": "http://localhost:8000",
                "server_cmd": "",
                "server_cwd": "",
                "start_together": False,
            }
        },
        "ssh_profiles": [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(v1_json, f)

    store = SettingsStore(path=path)

    def _boom():
        raise OSError("disk full")

    monkeypatch.setattr(store, "save", _boom)

    s = store.load()  # 예외를 raise하지 않아야 함
    meta = s.tunnels["mysite"]
    assert len(meta.routes) == 1
    assert meta.routes[0].hostname == "mysite.example.com"
