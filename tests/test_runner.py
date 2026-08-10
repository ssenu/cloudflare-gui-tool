import sys
import time
from app.core.runner import LocalRunner


def test_run_captures_stdout_and_exit_code():
    r = LocalRunner()
    res = r.run([sys.executable, "-c", "print('hello')"])
    assert res.exit_code == 0
    assert "hello" in res.stdout


def test_run_captures_stderr_nonzero():
    r = LocalRunner()
    res = r.run([sys.executable, "-c", "import sys; sys.stderr.write('bad'); sys.exit(3)"])
    assert res.exit_code == 3
    assert "bad" in res.stderr


def test_spawn_streams_lines_and_exit():
    r = LocalRunner()
    lines, exits = [], []
    p = r.spawn(
        [sys.executable, "-u", "-c", "print('l1'); print('l2')"],
        on_line=lambda s, l: lines.append((s, l)),
        on_exit=lambda c: exits.append(c))
    for _ in range(100):
        if exits:
            break
        time.sleep(0.05)
    assert exits == [0]
    assert ("stdout", "l1") in lines and ("stdout", "l2") in lines
    assert not p.is_running()


def test_spawn_stop_terminates():
    r = LocalRunner()
    p = r.spawn([sys.executable, "-c", "import time; time.sleep(60)"])
    assert p.is_running()
    p.stop()
    assert not p.is_running()


def test_file_ops(tmp_path):
    r = LocalRunner()
    f = str(tmp_path / "a.txt")
    assert not r.file_exists(f)
    r.write_file(f, "내용")
    assert r.file_exists(f)
    assert r.read_file(f) == "내용"
    r.remove_file(f)
    assert not r.file_exists(f)
