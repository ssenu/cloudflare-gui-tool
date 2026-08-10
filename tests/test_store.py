import json
from app.core.store import SettingsStore, TunnelMeta, SshProfile


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
        name="mysite", hostname="mysite.example.com",
        service="http://localhost:8000",
        server_cmd="uvicorn main:app --port 8000",
        server_cwd="C:/proj", start_together=True)
    store.settings.ssh_profiles.append(
        SshProfile(name="rpi", host="192.168.0.10", port=22,
                   username="pi", key_path="C:/keys/id_ed25519"))
    store.save()

    loaded = SettingsStore(path=path).load()
    assert loaded.root_domain == "example.com"
    assert loaded.tunnels["mysite"].start_together is True
    assert loaded.ssh_profiles[0].host == "192.168.0.10"
    # 파일이 사람이 읽을 수 있는 JSON인지
    raw = json.loads(open(path, encoding="utf-8").read())
    assert "tunnels" in raw


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
                "hostname": "good.example.com",
                "service": "http://localhost:8000",
                "server_cmd": "",
                "server_cwd": "",
                "start_together": False
            },
            "missing_name": {
                # name이 없음 - 유효하지 않음
                "hostname": "bad.example.com"
            },
            "extra_field": {
                "name": "extra",
                "hostname": "extra.example.com",
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
