from app.core.git_repo import (
    STATE_CLONING,
    STATE_FAILED,
    STATE_NONE,
    STATE_READY,
    GitClient,
    clone_argv,
    clone_state,
    join_path,
    parse_repo_name,
    validate_repo_name,
    validate_repo_url,
)
from app.core.run_registry import RunRegistry
from app.core.store import RepoMeta
from tests.fake_runner import FakeRunner


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
