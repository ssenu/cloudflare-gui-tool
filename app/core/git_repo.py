"""Git 저장소 클론/상태 판정을 위한 순수 함수 + runner 기반 클라이언트.

core 모듈이므로 PyQt6에 의존하지 않는다. 클론은 오래 걸릴 수 있어
CommandRunner.run()으로 동기 실행하지 않고, spawn_detached + RunRegistry의
PID/로그 파일 방식(터널/서비스와 동일)으로 처리한다.
"""
from __future__ import annotations

import re

from app.core.run_registry import RunRegistry
from app.core.runner import CommandRunner

_URL_RE = re.compile(
    r"^(https?://\S+|ssh://\S+|[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:\S+)$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# 클론 상태
STATE_NONE = "none"
STATE_CLONING = "cloning"
STATE_READY = "ready"
STATE_FAILED = "failed"


def parse_repo_name(url: str) -> str:
    """URL에서 폴더 이름을 추출한다.

    ``https://github.com/a/b.git`` -> ``b``, ``git@github.com:a/b.git`` -> ``b``.
    끝의 슬래시와 ``.git``을 제거한다. 추출 실패 시 빈 문자열.
    """
    url = url.strip()
    if not url:
        return ""
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[: -len(".git")]
    # 마지막 '/' 또는 ':' 뒤가 이름
    tail = re.split(r"[/:]", url)[-1] if url else ""
    tail = tail.strip()
    if not tail or not _NAME_RE.match(tail):
        return ""
    return tail


def validate_repo_url(url: str) -> str:
    """Git 주소 형식을 검증한다. 오류 메시지 또는 빈 문자열."""
    url = url.strip()
    if not url:
        return "Git 주소를 입력하세요"
    if not _URL_RE.match(url):
        return ("Git 주소 형식이 올바르지 않습니다 "
                "(https://, git@host:path, ssh:// 형식만 허용)")
    return ""


def validate_repo_name(name: str, existing: list[str]) -> str:
    """저장소(폴더) 이름을 검증한다. 오류 메시지 또는 빈 문자열."""
    name = name.strip()
    if not name:
        return "이름을 입력하세요"
    if not _NAME_RE.match(name):
        return "영문, 숫자, -, _, . 만 사용할 수 있습니다"
    if name in existing:
        return "같은 이름의 프로젝트가 이미 있습니다"
    return ""


def join_path(root: str, name: str) -> str:
    """POSIX 경로 결합 (항상 '/' 사용, 중복 슬래시 제거)."""
    root = root.rstrip("/")
    name = name.strip("/")
    if not root:
        return "/" + name if name else "/"
    return f"{root}/{name}"


def clone_argv(url: str, dest: str, branch: str = "") -> list[str]:
    """git clone 명령을 구성한다. shallow clone(--depth)은 쓰지 않는다 -
    이후 git pull로 업데이트해야 하기 때문이다."""
    argv = ["git", "clone", url, dest]
    if branch:
        argv += ["--branch", branch]
    return argv


class GitClient:
    """runner를 통해 대상 머신의 git 저장소를 조회하는 클라이언트."""

    def __init__(self, runner: CommandRunner):
        self.runner = runner

    def is_repo(self, path: str) -> bool:
        return self.runner.file_exists(path.rstrip("/") + "/.git")

    def current_commit(self, path: str) -> str:
        try:
            res = self.runner.run(["git", "-C", path, "rev-parse", "--short", "HEAD"])
        except Exception:
            return ""
        if res.exit_code != 0:
            return ""
        return res.stdout.strip()

    def current_branch(self, path: str) -> str:
        try:
            res = self.runner.run(
                ["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"])
        except Exception:
            return ""
        if res.exit_code != 0:
            return ""
        return res.stdout.strip()

    def pull_argv(self, path: str) -> list[str]:
        return ["git", "-C", path, "pull", "--ff-only"]


def clone_state(reg: RunRegistry, git: GitClient, repo) -> str:
    """클론 진행 상태를 판정한다.

    - PID 파일이 있고 살아있으면 "cloning"
    - PID 파일이 없고 저장소면 "ready"
    - PID 파일이 없고 저장소가 아니면 "failed" (한 번도 시작 안 했으면 "none")
    - 클론이 끝났으면(PID 죽음) PID 파일을 정리한다
    """
    unit = reg.unit_clone(repo.id)
    pid = reg.read_pid(unit)
    if pid is not None:
        alive = reg.runner.pids_alive([pid])
        if pid in alive:
            return STATE_CLONING
        # 죽은 PID 파일은 정리한다
        reg.clear_pid(unit)
    if git.is_repo(repo.path):
        return STATE_READY
    # 로그가 있었다면(클론을 시도했었다면) 실패로 본다
    if reg.runner.file_exists(reg.log_path(unit)):
        return STATE_FAILED
    return STATE_NONE
