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


def validate_repo_root(path: str) -> str:
    """클론 기본 위치(repo_root)를 검증한다. 오류 메시지 또는 빈 문자열.

    이 값은 이후 join_path()로 클론 목적지를, "폴더까지 삭제"에서는 그
    목적지를 그대로 rm -rf 인자로 넘기는 데 쓰인다 - 상대경로나 ".."가
    섞이면 삭제가 의도한 위치 밖으로 나갈 수 있어 이름(name)과 같은
    수준으로 엄격히 막는다.
    """
    path = path.strip()
    if not path:
        return "클론 위치를 입력하세요"
    if not path.startswith("/"):
        return "절대 경로여야 합니다 (예: /srv/apps)"
    segments = path.split("/")
    if ".." in segments:
        return "경로에 '..'를 포함할 수 없습니다"
    return ""


def ensure_repo_root(runner: CommandRunner, repo_root: str) -> bool:
    """repo_root 디렉터리가 있는지 확인하고, 없으면 만들어본다.

    성공적으로 존재하게 되면 True. SSH의 ensure_dir(``mkdir -p``)는 권한
    부족 등으로 실패해도 예외를 던지지 않으므로(로컬 ensure_dir은 예외를
    던질 수 있음), 시도 후 실제로 존재하는지 다시 확인해서 판정한다 -
    두 러너의 실패 방식이 달라도 호출자는 반환값 하나만 보면 된다.
    """
    try:
        runner.ensure_dir(repo_root)
    except Exception:
        pass
    return runner.file_exists(repo_root)


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


def clone_state(reg: RunRegistry, git: GitClient, repo,
                cache: dict[str, dict] | None = None) -> str:
    """클론 진행 상태를 판정한다.

    - PID가 있고 살아있으면 "cloning" - 단, 기록된 명령 토큰(``git``)과
      실제 실행 중인 명령이 다르면(PID 재사용 의심) "failed"로 판정한다.
      조회 자체가 실패(원격 연결 문제 등)하면 fail-open: 대조를 건너뛰고
      살아있는 것으로 본다. 한 번 일치가 확인된 PID는 캐시해 매 틱
      재조회하지 않는다.
    - PID가 없거나 막 죽었으면: ``is_repo()``이고 **동시에**
      ``current_commit()``이 값을 반환할 때만 "ready" - git clone은 아주
      이른 시점에 ``.git``을 만들기 때문에, 중간에 끊긴 클론도 ``.git``
      존재만으로는 "완료"로 오판하기 쉽다. 둘 중 하나라도 아니면 로그가
      있었으면(시도한 적 있으면) "failed", 없으면 "none".
    - "ready"로 한 번 확정된 유닛은 결과를 캐시해 이후로는 원격 조회 없이
      즉시 반환한다(안정된 항목의 폴링 비용을 없앤다).
    - 죽은 PID 파일은 정리한다.

    ``cache``는 호출자가 여러 틱에 걸쳐 들고 있는 dict(``repo.id`` 키)다.
    넘기지 않으면 매 호출이 새 캐시로 취급되어 캐싱 이점이 없다 - UI는
    반드시 같은 dict를 재사용해야 한다.
    """
    if cache is None:
        cache = {}
    entry = cache.setdefault(repo.id, {})

    if entry.get("ready"):
        return STATE_READY

    unit = reg.unit_clone(repo.id)
    record = reg.read_record(unit)
    if record is not None:
        pid, cmd_token = record
        alive = pid in reg.runner.pids_alive([pid])
        if alive:
            if cmd_token and entry.get("cmd_verified_pid") != pid:
                cmdlines = reg.runner.pid_cmdlines([pid])
                if cmdlines is None:
                    pass  # fail-open: 조회 실패 - 대조 건너뛰고 살아있는 것으로 간주
                elif pid in cmdlines:
                    if cmd_token.strip().lower() in cmdlines[pid].strip().lower():
                        entry["cmd_verified_pid"] = pid
                    else:
                        # PID 재사용 의심: 이 PID는 더 이상 우리 클론이 아니다.
                        reg.clear_pid(unit)
                        return STATE_FAILED
                # else: 이번 조회 응답에 이 PID가 없었다 - 모름으로 보류
            return STATE_CLONING
        # 죽은 PID 파일은 정리한다
        reg.clear_pid(unit)

    if git.is_repo(repo.path):
        commit = git.current_commit(repo.path)
        if commit:
            entry["ready"] = True
            entry["commit"] = commit
            return STATE_READY
    # 로그가 있었다면(클론을 시도했었다면) 실패로 본다
    if reg.runner.file_exists(reg.log_path(unit)):
        return STATE_FAILED
    return STATE_NONE
