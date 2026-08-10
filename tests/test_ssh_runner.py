import time
from app.core.ssh_runner import quote_cmd, SshProcess
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


# Fake channel for testing SshProcess without real SSH
class FakeChannel:
    def __init__(self, data_chunks=None, recv_raises=None, exit_status=0):
        self.data_chunks = data_chunks or []
        self.recv_raises = recv_raises
        self.exit_status = exit_status
        self.closed = False
        self._chunk_index = 0
        self._exit_status_ready = False

    def recv(self, bufsize):
        if self.recv_raises:
            raise self.recv_raises
        if self._chunk_index >= len(self.data_chunks):
            self._exit_status_ready = True
            return b""
        chunk = self.data_chunks[self._chunk_index]
        self._chunk_index += 1
        return chunk

    def close(self):
        self.closed = True

    def recv_exit_status(self):
        return self.exit_status

    def exit_status_ready(self):
        return self._exit_status_ready


def test_pump_lines_and_exit():
    """Test that pump correctly extracts lines and calls on_exit."""
    lines = []
    exit_codes = []

    def on_line(stream, line):
        lines.append(line)

    def on_exit(code):
        exit_codes.append(code)

    fake_ch = FakeChannel(data_chunks=[b"l1\n", b"l2\n"], exit_status=0)
    proc = SshProcess(fake_ch, on_line, on_exit)

    # Give daemon thread time to process
    time.sleep(0.5)

    assert lines == ["l1", "l2"], f"Expected ['l1', 'l2'], got {lines}"
    assert exit_codes == [0], f"Expected [0], got {exit_codes}"


def test_pump_exception_still_calls_on_exit():
    """Test that pump calls on_exit even if recv() raises exception."""
    exit_codes = []

    def on_exit(code):
        exit_codes.append(code)

    fake_ch = FakeChannel(recv_raises=OSError("connection lost"))
    proc = SshProcess(fake_ch, None, on_exit)

    # Give daemon thread time to process and call on_exit
    time.sleep(0.5)

    # on_exit must have been called exactly once with code 1 (error)
    assert len(exit_codes) == 1, f"on_exit not called, got {exit_codes}"
    assert exit_codes[0] == 1, f"Expected error code 1, got {exit_codes[0]}"
