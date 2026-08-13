"""대상 준비물(cloudflared/git/docker) 점검 로직."""
from __future__ import annotations

from app.core.prereq import (all_ok, check_prereqs, has_warning, summary)
from app.core.runner import RunResult
from tests.fake_runner import FakeRunner

CF = ("cloudflared", "--version")
GIT = ("git", "--version")
DOCKER = ("docker", "--version")
COMPOSE = ("docker", "compose", "version")


def runner_with(*, cloudflared=True, git=True, docker=True, compose=True,
                cert=True) -> FakeRunner:
    r = FakeRunner(home="/home/pi")
    r.run_results[CF] = (RunResult(0, "cloudflared version 2026.7.3", "")
                         if cloudflared else RunResult(127, "", "command not found"))
    r.run_results[GIT] = (RunResult(0, "git version 2.53.0", "")
                          if git else RunResult(127, "", "command not found"))
    r.run_results[DOCKER] = (RunResult(0, "Docker version 29.7.2", "")
                             if docker else RunResult(127, "", "command not found"))
    r.run_results[COMPOSE] = (RunResult(0, "Docker Compose version v5.4.0", "")
                              if compose else RunResult(1, "", "unknown command"))
    if cert:
        r.files["/home/pi/.cloudflared/cert.pem"] = "x"
    return r


def test_all_present():
    results = check_prereqs(runner_with())
    assert [r.key for r in results] == ["cloudflared", "git", "docker"]
    assert all_ok(results)
    assert not has_warning(results)
    assert "모두 준비" in summary(results)
    assert "2026.7.3" in results[0].detail  # 버전을 보여준다


def test_missing_git_is_reported_with_install_command():
    results = check_prereqs(runner_with(git=False))
    git = next(r for r in results if r.key == "git")

    assert not git.ok
    assert not all_ok(results)
    assert "apt install" in " ".join(git.install)
    assert "클론" in git.why           # 없으면 무엇이 안 되는지 알려준다
    assert "git" in summary(results)


def test_missing_all_three():
    results = check_prereqs(runner_with(cloudflared=False, git=False, docker=False))
    assert [r.ok for r in results] == [False, False, False]
    assert "cloudflared" in summary(results) and "docker" in summary(results)


def test_cloudflared_without_login_is_warned_but_ok():
    """설치는 됐는데 cert.pem이 없으면 터널을 만들 수 없다 - 미리 알려준다."""
    results = check_prereqs(runner_with(cert=False))
    cf = next(r for r in results if r.key == "cloudflared")

    assert cf.ok                       # 설치 자체는 되어 있으므로 통과
    assert "cloudflared tunnel login" in cf.warning
    assert has_warning(results)
    assert "마무리할 준비물" in summary(results)


def test_docker_without_compose_plugin_is_warned():
    results = check_prereqs(runner_with(compose=False))
    docker = next(r for r in results if r.key == "docker")

    assert docker.ok
    assert "compose" in docker.warning


def test_probe_failure_is_treated_as_missing():
    """연결이 끊기는 등 실행 자체가 실패해도 '없음'으로 처리하고 죽지 않는다."""
    r = runner_with()

    def boom(cmd, timeout=60.0, cwd=None):
        raise OSError("연결 끊김")

    r.run = boom
    results = check_prereqs(r)

    assert not all_ok(results)
    assert "연결 끊김" in results[0].detail


def test_custom_cloudflared_binary_is_used():
    r = runner_with()
    r.run_results[("C:/tools/cloudflared.exe", "--version")] = RunResult(
        0, "cloudflared version 1.0", "")

    results = check_prereqs(r, "C:/tools/cloudflared.exe")

    assert results[0].ok
    assert "1.0" in results[0].detail
