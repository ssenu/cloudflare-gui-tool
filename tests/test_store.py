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
