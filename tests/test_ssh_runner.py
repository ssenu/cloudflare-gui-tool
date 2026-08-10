from app.core.ssh_runner import quote_cmd
from app.core.store import SshProfile
from app.core.ssh_runner import SshRunner


def test_quote_cmd_spaces_and_specials():
    assert quote_cmd(["echo", "hello world"]) == "echo 'hello world'"
    assert quote_cmd(["cloudflared", "tunnel", "run", "my-tunnel"]) == \
        "cloudflared tunnel run my-tunnel"


def test_runner_name_includes_profile():
    r = SshRunner(SshProfile(name="rpi", host="1.2.3.4"))
    assert r.name == "ssh:rpi"
    assert not r.is_connected()
