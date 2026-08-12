from app.core.git_repo import (
    STATE_CLONING,
    STATE_FAILED,
    STATE_NONE,
    STATE_READY,
    GitClient,
    clone_argv,
    clone_state,
    ensure_repo_root,
    join_path,
    parse_repo_name,
    validate_repo_name,
    validate_repo_root,
    validate_repo_url,
)
from app.core.run_registry import RunRegistry
from app.core.runner import RunResult
from app.core.store import RepoMeta
from tests.fake_runner import FakeRunner


def _set_commit(runner: FakeRunner, path: str, commit: str = "abc1234"):
    runner.run_results[("git", "-C", path, "rev-parse", "--short", "HEAD")] = \
        RunResult(0, commit + "\n", "")


# ---- parse_repo_name ----

def test_parse_repo_name_https():
    assert parse_repo_name("https://github.com/a/b.git") == "b"


def test_parse_repo_name_ssh_shorthand():
    assert parse_repo_name("git@github.com:a/b.git") == "b"


def test_parse_repo_name_no_git_suffix():
    assert parse_repo_name("https://github.com/a/b") == "b"


def test_parse_repo_name_trailing_slash():
    assert parse_repo_name("https://github.com/a/b/") == "b"


def test_parse_repo_name_empty():
    assert parse_repo_name("") == ""


def test_parse_repo_name_invalid():
    assert parse_repo_name("not a url at all!!") == ""


# ---- validate_repo_url ----

def test_validate_repo_url_empty():
    assert validate_repo_url("") != ""


def test_validate_repo_url_https_ok():
    assert validate_repo_url("https://github.com/a/b.git") == ""


def test_validate_repo_url_http_ok():
    assert validate_repo_url("http://example.com/a/b.git") == ""


def test_validate_repo_url_ssh_shorthand_ok():
    assert validate_repo_url("git@github.com:a/b.git") == ""


def test_validate_repo_url_ssh_scheme_ok():
    assert validate_repo_url("ssh://git@github.com/a/b.git") == ""


def test_validate_repo_url_bad_format():
    assert validate_repo_url("not a url") != ""


# ---- validate_repo_name ----

def test_validate_repo_name_ok():
    assert validate_repo_name("myapp", []) == ""


def test_validate_repo_name_empty():
    assert validate_repo_name("", []) != ""


def test_validate_repo_name_bad_chars():
    assert validate_repo_name("my app!", []) != ""


def test_validate_repo_name_duplicate():
    assert validate_repo_name("myapp", ["myapp"]) != ""


# ---- join_path ----

def test_join_path_basic():
    assert join_path("/srv/apps", "blog") == "/srv/apps/blog"


def test_join_path_trailing_slash_root():
    assert join_path("/srv/apps/", "blog") == "/srv/apps/blog"


def test_join_path_slashes_in_name():
    assert join_path("/srv/apps", "/blog/") == "/srv/apps/blog"


# ---- clone_argv ----

def test_clone_argv_no_branch():
    assert clone_argv("https://github.com/a/b.git", "/srv/apps/b") == [
        "git", "clone", "https://github.com/a/b.git", "/srv/apps/b"]


def test_clone_argv_with_branch():
    assert clone_argv("https://github.com/a/b.git", "/srv/apps/b", "main") == [
        "git", "clone", "https://github.com/a/b.git", "/srv/apps/b",
        "--branch", "main"]


def test_clone_argv_no_shallow_flag():
    argv = clone_argv("https://github.com/a/b.git", "/srv/apps/b")
    assert "--depth" not in argv


# ---- GitClient ----

def test_is_repo_true():
    runner = FakeRunner()
    runner.files["/srv/apps/b/.git"] = ""
    client = GitClient(runner)
    assert client.is_repo("/srv/apps/b") is True


def test_is_repo_false():
    runner = FakeRunner()
    client = GitClient(runner)
    assert client.is_repo("/srv/apps/b") is False


def test_current_commit_success():
    runner = FakeRunner()
    from app.core.runner import RunResult
    runner.run_results[("git", "-C", "/srv/apps/b", "rev-parse", "--short", "HEAD")] = \
        RunResult(0, "abc1234\n", "")
    client = GitClient(runner)
    assert client.current_commit("/srv/apps/b") == "abc1234"


def test_current_commit_failure_returns_empty():
    runner = FakeRunner()
    from app.core.runner import RunResult
    runner.run_results[("git", "-C", "/srv/apps/b", "rev-parse", "--short", "HEAD")] = \
        RunResult(1, "", "not a git repository")
    client = GitClient(runner)
    assert client.current_commit("/srv/apps/b") == ""


def test_pull_argv():
    runner = FakeRunner()
    client = GitClient(runner)
    assert client.pull_argv("/srv/apps/b") == ["git", "-C", "/srv/apps/b", "pull", "--ff-only"]


# ---- clone_state ----

def _repo(path="/srv/apps/b"):
    return RepoMeta(id="aaaa1111", name="b", url="https://github.com/a/b.git", path=path)


def test_clone_state_none_when_never_started():
    runner = FakeRunner()
    reg = RunRegistry(runner)
    git = GitClient(runner)
    repo = _repo()
    assert clone_state(reg, git, repo) == STATE_NONE


def test_clone_state_cloning_when_pid_alive():
    runner = FakeRunner()
    reg = RunRegistry(runner)
    git = GitClient(runner)
    repo = _repo()
    unit = reg.unit_clone(repo.id)
    pid = runner.spawn_detached(["git", "clone"], None, reg.log_path(unit))
    reg.write_pid(unit, pid, cmd="git")
    assert clone_state(reg, git, repo) == STATE_CLONING


def test_clone_state_ready_when_pid_gone_and_repo_exists():
    runner = FakeRunner()
    reg = RunRegistry(runner)
    git = GitClient(runner)
    repo = _repo()
    runner.files[repo.path + "/.git"] = ""
    _set_commit(runner, repo.path)
    assert clone_state(reg, git, repo) == STATE_READY


def test_clone_state_failed_when_pid_gone_and_not_repo_but_log_exists():
    runner = FakeRunner()
    reg = RunRegistry(runner)
    git = GitClient(runner)
    repo = _repo()
    unit = reg.unit_clone(repo.id)
    pid = runner.spawn_detached(["git", "clone"], None, reg.log_path(unit))
    reg.write_pid(unit, pid, cmd="git")
    runner.live_pids.discard(pid)  # 클론 프로세스 종료(실패)
    assert clone_state(reg, git, repo) == STATE_FAILED


def test_clone_state_clears_dead_pid_file():
    runner = FakeRunner()
    reg = RunRegistry(runner)
    git = GitClient(runner)
    repo = _repo()
    unit = reg.unit_clone(repo.id)
    pid = runner.spawn_detached(["git", "clone"], None, reg.log_path(unit))
    reg.write_pid(unit, pid, cmd="git")
    runner.live_pids.discard(pid)
    clone_state(reg, git, repo)
    assert reg.read_pid(unit) is None


# ---- I2: 중단된 클론(.git은 있으나 커밋 없음)을 ready로 오판하지 않는다 ----

def test_clone_state_git_dir_without_commit_is_failed_not_ready():
    """PID가 죽었고 .git은 있지만(중단된 클론의 흔적) HEAD 커밋을 얻지
    못하면 ready가 아니라 failed여야 한다."""
    runner = FakeRunner()
    reg = RunRegistry(runner)
    git = GitClient(runner)
    repo = _repo()
    unit = reg.unit_clone(repo.id)
    pid = runner.spawn_detached(["git", "clone"], None, reg.log_path(unit))
    reg.write_pid(unit, pid, cmd="git")
    runner.live_pids.discard(pid)  # 네트워크 끊김 등으로 중단
    runner.files[repo.path + "/.git"] = ""  # git clone이 아주 이른 시점에 만든 흔적
    # current_commit()은 기본 run_results(성공, 빈 stdout)라 빈 문자열을 반환한다
    assert clone_state(reg, git, repo) == STATE_FAILED


def test_clone_state_ready_result_is_cached_and_not_rechecked():
    """한 번 ready로 확정되면 이후 호출은 원격 조회 없이 캐시를 반환한다."""
    runner = FakeRunner()
    reg = RunRegistry(runner)
    git = GitClient(runner)
    repo = _repo()
    runner.files[repo.path + "/.git"] = ""
    _set_commit(runner, repo.path)
    cache: dict = {}
    assert clone_state(reg, git, repo, cache) == STATE_READY
    calls_after_first = len(runner.run_calls)
    assert clone_state(reg, git, repo, cache) == STATE_READY
    assert len(runner.run_calls) == calls_after_first  # 추가 원격 왕복 없음


# ---- I3: repo_root 검증 ----

def test_validate_repo_root_empty():
    assert validate_repo_root("") != ""


def test_validate_repo_root_relative_rejected():
    assert validate_repo_root("srv/apps") != ""


def test_validate_repo_root_dotdot_rejected():
    assert validate_repo_root("/srv/../etc") != ""


def test_validate_repo_root_valid():
    assert validate_repo_root("/srv/apps") == ""


# ---- C1: repo_root 부모 디렉터리 생성 ----

def test_ensure_repo_root_creates_and_returns_true():
    runner = FakeRunner()
    assert runner.file_exists("/srv/apps") is False
    assert ensure_repo_root(runner, "/srv/apps") is True
    assert runner.file_exists("/srv/apps") is True


def test_ensure_repo_root_returns_false_when_blocked():
    """SSH의 mkdir -p가 권한 부족으로 조용히 실패하는 상황을 재현한다."""
    runner = FakeRunner()
    runner.ensure_dir_blocked.add("/srv/apps")
    assert ensure_repo_root(runner, "/srv/apps") is False
    assert runner.file_exists("/srv/apps") is False


# ---- I4: PID 재사용 대조 ----

def test_clone_state_pid_reused_with_different_command_is_failed():
    runner = FakeRunner()
    reg = RunRegistry(runner)
    git = GitClient(runner)
    repo = _repo()
    unit = reg.unit_clone(repo.id)
    pid = runner.spawn_detached(["git", "clone"], None, reg.log_path(unit))
    reg.write_pid(unit, pid, cmd="git")
    # PID가 재사용되어 지금은 전혀 다른 프로세스(예: nginx)가 실행 중
    runner.pid_cmdlines_map[pid] = "nginx"
    assert clone_state(reg, git, repo) == STATE_FAILED
    assert reg.read_pid(unit) is None  # PID 파일이 정리되었는지


def test_clone_state_pid_cmdlines_lookup_failure_is_fail_open():
    """조회 자체가 실패(None)하면 대조를 건너뛰고 계속 cloning으로 본다."""
    runner = FakeRunner()
    reg = RunRegistry(runner)
    git = GitClient(runner)
    repo = _repo()
    unit = reg.unit_clone(repo.id)
    pid = runner.spawn_detached(["git", "clone"], None, reg.log_path(unit))
    reg.write_pid(unit, pid, cmd="git")
    runner.pid_cmdlines_fail = True
    assert clone_state(reg, git, repo) == STATE_CLONING
    assert reg.read_pid(unit) == pid  # PID 파일이 그대로 남아있어야 함


def test_clone_state_cmd_verification_happens_once_per_pid():
    """일치가 확인된 PID는 매 틱 재조회하지 않는다."""
    runner = FakeRunner()
    reg = RunRegistry(runner)
    git = GitClient(runner)
    repo = _repo()
    unit = reg.unit_clone(repo.id)
    pid = runner.spawn_detached(["git", "clone"], None, reg.log_path(unit))
    reg.write_pid(unit, pid, cmd="git")
    cache: dict = {}
    assert clone_state(reg, git, repo, cache) == STATE_CLONING
    calls_after_first = runner.pid_cmdlines_calls
    assert clone_state(reg, git, repo, cache) == STATE_CLONING
    assert clone_state(reg, git, repo, cache) == STATE_CLONING
    assert runner.pid_cmdlines_calls == calls_after_first  # 재조회 없음
