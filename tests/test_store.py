import json
import os
import time

from app.core.store import (RepoMeta, RouteMeta, ServiceSpec, SettingsStore,
                            SshProfile, TunnelMeta, new_route_id)


def test_load_missing_file_returns_defaults(tmp_path):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    s = store.load()
    assert s.root_domain == ""
    assert s.tunnels_for("local") == {}
    assert s.ssh_profiles == []


def test_save_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()
    store.settings.root_domain = "example.com"
    store.settings.tunnels_for("local")["mysite"] = TunnelMeta(
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
    assert loaded.tunnels_for("local")["mysite"].routes[0].server.autostart is True
    assert loaded.ssh_profiles[0].host == "192.168.0.10"
    # 파일이 사람이 읽을 수 있는 JSON인지
    raw = json.loads(open(path, encoding="utf-8").read())
    assert "targets" in raw
    assert "local" in raw["targets"]


def test_tunnel_owners_roundtrip(tmp_path):
    # C1: owner는 대상별 targets가 아니라 계정 단위(터널 UUID 키) 맵에
    # 저장된다 - 대상을 바꿔도 같은 값을 봐야 하므로 targets 밖에 있어야 한다.
    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()
    store.settings.tunnel_owners["a1b2c3d4-uuid"] = "ssh:webPi"
    store.save()

    loaded = SettingsStore(path=path).load()
    assert loaded.tunnel_owners == {"a1b2c3d4-uuid": "ssh:webPi"}
    raw = json.loads(open(path, encoding="utf-8").read())
    assert raw["tunnel_owners"] == {"a1b2c3d4-uuid": "ssh:webPi"}


def test_tunnel_owners_defaults_to_empty_when_absent_from_old_config(tmp_path):
    """tunnel_owners 키가 없던 구버전 설정 파일도 문제없이 로드되어야 함"""
    path = str(tmp_path / "settings.json")
    old_format = {
        "root_domain": "example.com",
        "targets": {
            "local": {
                "mysite": {"name": "mysite", "routes": []},
            }
        },
        # tunnel_owners 키 자체가 없음 (구버전)
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(old_format, f)

    store = SettingsStore(path=path)
    s = store.load()
    assert s.tunnel_owners == {}


def test_tunnel_owners_ignores_non_string_entries(tmp_path):
    path = str(tmp_path / "settings.json")
    bad_format = {
        "tunnel_owners": {
            "good-uuid": "local",
            "bad-uuid": 123,       # 값이 문자열이 아님 -> 스킵
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bad_format, f)

    store = SettingsStore(path=path)
    s = store.load()
    assert s.tunnel_owners == {"good-uuid": "local"}


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
    assert s.tunnels_for("local") == {}
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
    assert "good" in s.tunnels_for("local")
    assert "extra_field" in s.tunnels_for("local")
    assert "missing_name" not in s.tunnels_for("local")
    assert len(s.tunnels_for("local")) == 2  # 유효하지 않은 항목 1개는 제외
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
    assert s.tunnels_for("local") == {}
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
    assert s.tunnels_for("local") == {}  # 형식이 잘못되었으므로 빈 dict
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

    meta = s.tunnels_for("local")["mysite"]
    assert len(meta.routes) == 1
    route = meta.routes[0]
    assert route.hostname == "mysite.example.com"
    assert route.service == "http://localhost:8000"
    assert route.server.kind == "command"
    assert route.server.start_cmd == "uvicorn main:app --port 8000"
    assert route.server.cwd == "C:/proj"
    assert route.server.autostart is True
    assert isinstance(route.id, str) and route.id

    # 파일이 v2 형식(routes 키) + targets 구조로 다시 저장되었는지 확인
    raw = json.loads(open(path, encoding="utf-8").read())
    assert "routes" in raw["targets"]["local"]["mysite"]
    assert "hostname" not in raw["targets"]["local"]["mysite"]


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
    assert s.tunnels_for("local")["empty"].routes == []


def test_v2_roundtrip_multiple_routes_with_docker_service(tmp_path):
    """v2 형식 왕복: 라우트 2개, 그중 1개는 도커 서비스."""
    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()
    store.settings.tunnels_for("local")["multi"] = TunnelMeta(
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
    meta = loaded.tunnels_for("local")["multi"]
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
    routes = s.tunnels_for("local")["mixed"].routes
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
    assert s.tunnels_for("local")["t"].routes[0].server.kind == "command"


def test_v2_only_file_load_does_not_rewrite_file(tmp_path):
    """v2 형식만 있는 파일은 load()가 파일을 다시 저장하지 않는다."""
    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()
    store.settings.tunnels_for("local")["t"] = TunnelMeta(
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
    route = s.tunnels_for("local")["t"].routes[0]
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
    route = s.tunnels_for("local")["t"].routes[0]
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
    routes = s.tunnels_for("local")["t"].routes
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
    meta = s.tunnels_for("local")["mysite"]
    assert len(meta.routes) == 1
    assert meta.routes[0].hostname == "mysite.example.com"


# ---- B1: 대상별 설정 분리 (targets) ----

def test_flat_tunnels_migrates_to_targets_local_and_resaves(tmp_path):
    """평평한 최상위 tunnels 형식을 로드하면 targets["local"]로 옮겨지고,
    파일이 새 nested 형식으로 다시 저장된다."""
    path = str(tmp_path / "settings.json")
    flat_json = {
        "root_domain": "example.com",
        "cloudflared_path": "",
        "tunnels": {
            "mysite": {
                "name": "mysite",
                "routes": [
                    {"id": "aaaa1111", "hostname": "mysite.example.com",
                     "service": "http://localhost:8000"},
                ],
            }
        },
        "ssh_profiles": [],
        "theme": "dark",
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(flat_json, f)

    store = SettingsStore(path=path)
    s = store.load()

    assert "local" in s.targets
    assert s.tunnels_for("local")["mysite"].routes[0].hostname == "mysite.example.com"

    raw = json.loads(open(path, encoding="utf-8").read())
    assert "targets" in raw
    assert "tunnels" not in raw
    assert raw["targets"]["local"]["mysite"]["routes"][0]["hostname"] == "mysite.example.com"


def test_tunnels_for_is_independent_per_target(tmp_path):
    """tunnels_for()는 대상마다 독립된 dict를 돌려주고, 한쪽을 수정해도 다른
    대상에는 영향을 주지 않는다."""
    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()

    local_tunnels = store.settings.tunnels_for("local")
    local_tunnels["mysite"] = TunnelMeta(name="mysite")

    ssh_tunnels = store.settings.tunnels_for("ssh:rpi")
    assert ssh_tunnels == {}
    assert "mysite" not in ssh_tunnels

    ssh_tunnels["other"] = TunnelMeta(name="other")
    assert "other" not in store.settings.tunnels_for("local")
    assert list(store.settings.tunnels_for("local").keys()) == ["mysite"]

    store.save()
    loaded = SettingsStore(path=path).load()
    assert list(loaded.tunnels_for("local").keys()) == ["mysite"]
    assert list(loaded.tunnels_for("ssh:rpi").keys()) == ["other"]


def test_tunnels_for_missing_target_returns_and_registers_empty_dict(tmp_path):
    """존재하지 않는 대상 키로 조회하면 빈 dict를 만들어 targets에 등록한다."""
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    store.load()
    assert "ssh:new" not in store.settings.targets
    result = store.settings.tunnels_for("ssh:new")
    assert result == {}
    assert "ssh:new" in store.settings.targets
    assert store.settings.targets["ssh:new"] is result


# ---- 라우트 이름(label) ----

def test_route_label_roundtrips(tmp_path):
    """RouteMeta.label이 저장/로드 왕복 후에도 유지된다."""
    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()
    store.settings.tunnels_for("local")["mysite"] = TunnelMeta(
        name="mysite",
        routes=[RouteMeta(id=new_route_id(), hostname="app.example.com",
                          service="http://localhost:8000", label="My App")],
    )
    store.save()

    loaded = SettingsStore(path=path).load()
    assert loaded.tunnels_for("local")["mysite"].routes[0].label == "My App"


def test_route_label_missing_in_old_file_defaults_to_empty(tmp_path):
    """label 키가 없는(구버전) settings.json도 문제없이 로드되고 빈 문자열로 채워진다."""
    path = str(tmp_path / "settings.json")
    v2_json = {
        "root_domain": "",
        "cloudflared_path": "",
        "tunnels": {
            "t": {
                "name": "t",
                "routes": [
                    {"id": "aaaa1111", "hostname": "h.example.com",
                     "service": "http://localhost:8000"},
                ],
            }
        },
        "ssh_profiles": [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(v2_json, f)

    store = SettingsStore(path=path)
    s = store.load()
    route = s.tunnels_for("local")["t"].routes[0]
    assert route.label == ""


def test_nested_targets_format_roundtrips(tmp_path):
    """이미 targets 형식인 파일은 그대로 로드/저장되며 재작성 트리거가 없다."""
    path = str(tmp_path / "settings.json")
    v2_json = {
        "root_domain": "",
        "cloudflared_path": "",
        "targets": {
            "local": {
                "a": {"name": "a", "routes": []},
            },
            "ssh:rpi": {
                "b": {"name": "b", "routes": []},
            },
        },
        "ssh_profiles": [],
        "theme": "dark",
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(v2_json, f)

    store = SettingsStore(path=path)
    save_calls = []
    store.save = lambda: save_calls.append(True)
    s = store.load()

    assert save_calls == []  # 이미 v2/targets 형식이므로 재저장 없음
    assert "a" in s.tunnels_for("local")
    assert "b" in s.tunnels_for("ssh:rpi")


# ---- 저장소(RepoMeta) ----

def test_repos_for_missing_target_returns_and_registers_empty_list(tmp_path):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    store.load()
    assert "ssh:new" not in store.settings.repos
    result = store.settings.repos_for("ssh:new")
    assert result == []
    assert "ssh:new" in store.settings.repos
    assert store.settings.repos["ssh:new"] is result


def test_repo_root_default(tmp_path):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    s = store.load()
    assert s.repo_root == "/srv/apps"


def test_repos_roundtrip(tmp_path):
    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()
    store.settings.repo_root = "/srv/apps"
    store.settings.repos_for("ssh:webpi").append(
        RepoMeta(id="aaaa1111", name="blog", url="https://github.com/a/blog.git",
                 path="/srv/apps/blog", branch="main"))
    store.save()

    loaded = SettingsStore(path=path).load()
    repos = loaded.repos_for("ssh:webpi")
    assert len(repos) == 1
    assert repos[0].name == "blog"
    assert repos[0].url == "https://github.com/a/blog.git"
    assert repos[0].path == "/srv/apps/blog"
    assert repos[0].branch == "main"
    assert loaded.repo_root == "/srv/apps"


def test_repos_missing_key_in_old_file_loads_empty(tmp_path):
    """repos 키가 없는(구버전) settings.json도 문제없이 로드되어야 한다."""
    path = str(tmp_path / "settings.json")
    old_json = {
        "root_domain": "example.com",
        "cloudflared_path": "",
        "tunnels": {},
        "ssh_profiles": [],
        "theme": "dark",
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(old_json, f)

    store = SettingsStore(path=path)
    s = store.load()
    assert s.repos_for("local") == []
    assert s.repo_root == "/srv/apps"


def test_repos_bad_entries_skipped(tmp_path):
    path = str(tmp_path / "settings.json")
    bad_json = {
        "root_domain": "",
        "cloudflared_path": "",
        "targets": {},
        "ssh_profiles": [],
        "repos": {
            "local": [
                {"id": "aaaa1111", "name": "good", "url": "https://x.com/a/good.git",
                 "path": "/srv/apps/good"},
                {"name": "no-id", "url": "https://x.com/a/b.git", "path": "/srv/apps/b"},
                {"id": "", "name": "empty-id", "url": "u", "path": "p"},
                "not_a_dict",
            ],
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bad_json, f)

    store = SettingsStore(path=path)
    s = store.load()
    repos = s.repos_for("local")
    assert len(repos) == 1
    assert repos[0].name == "good"


# ---- 준비물 점검 생략 목록 ----

def test_prereq_skip_round_trips(tmp_path):
    store = SettingsStore(path=str(tmp_path / "s.json"))
    store.load()
    store.settings.set_skip_prereq_check("ssh:webPi", True)
    store.save()

    reloaded = SettingsStore(path=str(tmp_path / "s.json")).load()
    assert reloaded.prereq_skip == ["ssh:webPi"]
    assert reloaded.skip_prereq_check("ssh:webPi")


def test_prereq_skip_can_be_turned_off(tmp_path):
    store = SettingsStore(path=str(tmp_path / "s.json"))
    store.load()
    store.settings.set_skip_prereq_check("ssh:a", True)
    store.settings.set_skip_prereq_check("ssh:a", False)
    assert store.settings.prereq_skip == []


def test_prereq_skip_ignores_non_string_entries(tmp_path):
    import json
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"prereq_skip": ["ssh:ok", 3, None]}),
                    encoding="utf-8")

    settings = SettingsStore(path=str(path)).load()

    assert settings.prereq_skip == ["ssh:ok"]


def test_missing_prereq_skip_field_loads_as_empty(tmp_path):
    import json
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"root_domain": "example.com"}), encoding="utf-8")

    settings = SettingsStore(path=str(path)).load()

    assert settings.prereq_skip == []


def test_duplicate_ssh_profile_names_are_dropped(tmp_path):
    """이름이 대상 키라서, 중복되면 같은 기기가 목록에 두 번 나온다."""
    import json
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"ssh_profiles": [
        {"name": "pi", "host": "10.0.0.1"},
        {"name": "pi", "host": "10.0.0.2"},
        {"name": "other", "host": "10.0.0.3"},
    ]}), encoding="utf-8")

    settings = SettingsStore(path=str(path)).load()

    assert [p.name for p in settings.ssh_profiles] == ["pi", "other"]
    assert settings.ssh_profiles[0].host == "10.0.0.1"  # 먼저 나온 것을 남긴다


def test_save_works_with_bare_filename(tmp_path, monkeypatch):
    """상대 경로(파일명만)로도 저장이 되어야 한다 - dirname이 빈 문자열이다."""
    monkeypatch.chdir(tmp_path)
    store = SettingsStore(path="only-name.json")
    store.load()
    store.save()
    assert (tmp_path / "only-name.json").exists()


# ---- 서버 카테고리 (터널 없는 서버 묶음) ----

def test_server_groups_roundtrip(tmp_path):
    """서버 카테고리는 대상별로 저장되고 그대로 다시 읽혀야 한다."""
    from app.core.store import ServerGroupMeta

    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()
    store.settings.server_groups_for("ssh:rpi").append(
        ServerGroupMeta(
            id="aabbccdd",
            name="백엔드",
            servers=[
                RouteMeta(
                    id="11223344",
                    hostname="",
                    service="http://localhost:8000",
                    label="api",
                    server=ServiceSpec(kind="docker", cwd="/srv/apps/api"),
                )
            ],
        ))
    store.save()

    loaded = SettingsStore(path=path).load()
    groups = loaded.server_groups_for("ssh:rpi")
    assert len(groups) == 1
    assert groups[0].name == "백엔드"
    assert groups[0].servers[0].label == "api"
    assert groups[0].servers[0].server.kind == "docker"
    assert groups[0].servers[0].hostname == ""
    # 다른 대상에는 새어 나가지 않는다
    assert loaded.server_groups_for("local") == []


def test_server_groups_skip_invalid_entries(tmp_path):
    """id/name이 없는 항목만 개별로 건너뛰고 나머지는 살린다."""
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"server_groups": {"local": [
        {"name": "이름만 있음"},                       # id 없음 -> 스킵
        {"id": "aabbccdd", "name": ""},                # name 빈 문자열 -> 스킵
        {"id": "11112222", "name": "정상", "servers": []},
    ]}}), encoding="utf-8")

    settings = SettingsStore(path=str(path)).load()

    assert [g.name for g in settings.server_groups_for("local")] == ["정상"]


def test_missing_server_group_fields_load_as_empty(tmp_path):
    """옛 설정 파일에는 없는 필드다 - 없어도 정상 로드되어야 한다."""
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"root_domain": "example.com"}), encoding="utf-8")

    settings = SettingsStore(path=str(path)).load()

    assert settings.server_groups == {}
    assert settings.card_order == {}


def test_card_order_roundtrip_and_validation(tmp_path):
    """카드 순서는 문자열 키/문자열 목록만 받아들인다."""
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"card_order": {
        "local": ["t:mysite", "s:aabbccdd", 7],   # 숫자 항목은 걸러진다
        "ssh:rpi": "문자열이라 목록이 아님",         # 통째로 스킵
        9: ["t:x"],                                # JSON에서 키는 문자열이 되지만 방어
    }}), encoding="utf-8")

    settings = SettingsStore(path=str(path)).load()

    assert settings.card_order["local"] == ["t:mysite", "s:aabbccdd"]
    assert "ssh:rpi" not in settings.card_order
