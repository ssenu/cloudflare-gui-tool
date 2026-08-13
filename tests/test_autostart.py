"""부팅 시 자동 실행(systemd 사용자 유닛)."""
from __future__ import annotations

import pytest

from app.core import autostart
from app.core.runner import RunResult
from tests.fake_runner import FakeRunner


def test_unit_name_is_sanitized():
    assert autostart.unit_name("web") == "cft-tunnel-web.service"
    assert autostart.unit_name("my site!") == "cft-tunnel-my-site-.service"


def test_unit_file_restarts_on_failure():
    text = autostart.build_unit("web", "/usr/bin/cloudflared tunnel run web")
    assert "Restart=always" in text          # 죽어도 systemd가 다시 띄운다
    assert "WantedBy=default.target" in text  # 부팅 시 자동 시작
    assert "After=network-online.target" in text
    assert "/usr/bin/cloudflared tunnel run web" in text


def test_quote_exec_quotes_paths_with_spaces():
    line = autostart.quote_exec(["cloudflared", "--config", "/home/a b/c.yml", "run"])
    assert '"/home/a b/c.yml"' in line
    assert line.startswith("cloudflared --config")


def test_enable_writes_unit_and_registers():
    runner = FakeRunner(home="/home/pi")
    autostart.enable(runner, "web", ["cloudflared", "tunnel", "run", "web"])

    path = "~/.config/systemd/user/cft-tunnel-web.service"
    assert path in runner.files
    assert "cloudflared tunnel run web" in runner.files[path]
    cmds = [" ".join(c) for c, _ in runner.run_calls]
    assert any("systemctl --user daemon-reload" in c for c in cmds)
    assert any("enable --now cft-tunnel-web.service" in c for c in cmds)
    # 로그아웃해도 계속 돌도록 linger를 켠다
    assert any("loginctl enable-linger" in c for c in cmds)


def test_enable_raises_when_systemctl_fails():
    runner = FakeRunner(home="/home/pi")
    runner.run_results[("systemctl", "--user", "enable", "--now",
                        "cft-tunnel-web.service")] = RunResult(1, "", "Failed to enable")

    with pytest.raises(autostart.AutostartError) as ex:
        autostart.enable(runner, "web", ["cloudflared", "tunnel", "run", "web"])

    assert "Failed to enable" in str(ex.value)


def test_disable_removes_unit_file():
    runner = FakeRunner(home="/home/pi")
    autostart.enable(runner, "web", ["cloudflared", "tunnel", "run", "web"])
    autostart.disable(runner, "web")

    assert "~/.config/systemd/user/cft-tunnel-web.service" not in runner.files
    cmds = [" ".join(c) for c, _ in runner.run_calls]
    assert any("disable --now cft-tunnel-web.service" in c for c in cmds)


def test_active_units_parses_one_line_per_unit():
    runner = FakeRunner(home="/home/pi")
    units = ["cft-tunnel-a.service", "cft-tunnel-b.service"]
    runner.run_results[("systemctl", "--user", "is-active", *units)] = RunResult(
        3, "active\ninactive\n", "")

    alive = autostart.active_units(runner, units)

    assert alive == {"cft-tunnel-a.service"}


def test_active_units_survives_failure():
    """조회가 실패하면 '아무것도 안 돈다'로 보고 죽지 않는다."""
    runner = FakeRunner(home="/home/pi")

    def boom(cmd, timeout=60.0, cwd=None):
        raise OSError("연결 끊김")

    runner.run = boom
    assert autostart.active_units(runner, ["x.service"]) == set()


def test_linger_enabled_reads_property():
    runner = FakeRunner(home="/home/pi")
    runner.run_results[("loginctl", "show-user", "--property=Linger")] = RunResult(
        0, "Linger=yes\n", "")
    assert autostart.linger_enabled(runner) is True

    runner.run_results[("loginctl", "show-user", "--property=Linger")] = RunResult(
        0, "Linger=no\n", "")
    assert autostart.linger_enabled(runner) is False


# ---- ProcessManager 연동: systemd가 관리하는 터널 ----

def test_boot_managed_tunnel_state_comes_from_systemd():
    from app.core.process_mgr import ProcessManager, TunnelState
    from app.core.run_registry import RunRegistry
    from app.core.store import TunnelMeta

    runner = FakeRunner(home="/home/pi")
    meta = TunnelMeta(name="web", boot_autostart=True)
    mgr = ProcessManager(lambda: RunRegistry(runner))

    runner.run_results[("systemctl", "--user", "is-active",
                        "cft-tunnel-web.service")] = RunResult(0, "active\n", "")
    mgr.refresh([meta])
    assert mgr.tunnel_state("web") == TunnelState.RUNNING

    runner.run_results[("systemctl", "--user", "is-active",
                        "cft-tunnel-web.service")] = RunResult(3, "inactive\n", "")
    mgr.refresh([meta])
    assert mgr.tunnel_state("web") == TunnelState.STOPPED


def test_boot_managed_start_uses_systemctl_not_spawn():
    """systemd가 관리하는 터널을 앱이 또 띄우면 같은 터널이 두 번 뜬다."""
    from app.core.cloudflared import CloudflaredClient
    from app.core.process_mgr import ProcessManager
    from app.core.run_registry import RunRegistry

    runner = FakeRunner(home="/home/pi")
    mgr = ProcessManager(lambda: RunRegistry(runner))

    mgr.start_tunnel("web", CloudflaredClient(runner), boot_managed=True)

    assert runner.spawn_detached_calls == []
    cmds = [" ".join(c) for c, _ in runner.run_calls]
    assert any("systemctl --user start cft-tunnel-web.service" in c for c in cmds)


def test_boot_managed_stop_uses_systemctl():
    from app.core.process_mgr import ProcessManager
    from app.core.run_registry import RunRegistry

    runner = FakeRunner(home="/home/pi")
    mgr = ProcessManager(lambda: RunRegistry(runner))

    mgr.stop_tunnel("web", boot_managed=True)

    cmds = [" ".join(c) for c, _ in runner.run_calls]
    assert any("systemctl --user stop cft-tunnel-web.service" in c for c in cmds)
    assert runner.kill_pid_calls == []
