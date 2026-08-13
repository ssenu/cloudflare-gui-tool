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


def test_remove_tree_removes_directory_and_contents(tmp_path):
    r = LocalRunner()
    d = tmp_path / "proj"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "f.txt").write_text("hi", encoding="utf-8")
    assert d.exists()
    r.remove_tree(str(d))
    assert not d.exists()


# ---- 로그 로테이션: 이어 읽는 지점이 글자 중간이면 처음부터 다시 ----

def test_tail_file_restarts_when_log_is_rewritten(tmp_path):
    """로그가 지워지고 다시 쓰였는데 새 크기가 더 크면 '줄어듦' 검사에 안 걸린다.

    그대로 이어 읽으면 글자 중간부터 읽어 화면에 깨진 문자가 찍힌다.
    """
    from app.core.runner import LocalRunner

    path = tmp_path / "a.log"
    # 바이트로 쓴다 - 윈도우 텍스트 모드는 \n을 \r\n으로 바꿔 바이트 수가 달라진다
    path.write_bytes("B-1\nB-2\n".encode())
    runner = LocalRunner()
    offset, text = runner.tail_file(str(path), 0)
    assert text == "B-1\nB-2\n"

    # 로테이션: 내용이 통째로 바뀌고 크기는 오히려 커졌다
    path.write_bytes("가나다라마바사\n".encode())
    assert path.stat().st_size > offset

    new_offset, new_text = runner.tail_file(str(path), offset)

    assert new_text == "가나다라마바사\n"      # 깨진 글자 없이
    assert new_offset == path.stat().st_size


def test_tail_file_keeps_following_normal_appends(tmp_path):
    """정상적으로 덧붙는 경우에는 이어 읽기가 그대로 동작해야 한다."""
    from app.core.runner import LocalRunner

    path = tmp_path / "a.log"
    path.write_bytes("한 줄\n".encode())
    runner = LocalRunner()
    offset, first = runner.tail_file(str(path), 0)
    with open(path, "ab") as f:
        f.write("두 번째 줄\n".encode())

    _off, second = runner.tail_file(str(path), offset)

    assert first == "한 줄\n"
    assert second == "두 번째 줄\n"   # 앞부분을 다시 읽지 않는다


def test_resumes_mid_character_detects_continuation_byte():
    from app.core.runner import resumes_mid_character

    assert resumes_mid_character("가".encode()[1:]) is True
    assert resumes_mid_character("가".encode()) is False
    assert resumes_mid_character(b"abc") is False
    assert resumes_mid_character(b"") is False
