"""대상 기기에 꼭 있어야 하는 프로그램 점검.

앱이 하는 일은 결국 대상 기기에서 cloudflared·git·docker를 대신 실행하는
것이다. 셋 중 하나라도 없으면 "왜 안 되는지 모르겠는" 실패로 나타난다 -
클론이 조용히 실패하거나(git 없음), 서버 토글이 켜졌다 꺼지거나(docker 없음),
터널 생성이 거부된다(cloudflared/인증서 없음). 그래서 대상에 붙는 시점에
한 번 확인해 미리 알려준다.

배포판을 가리지 않도록 판정은 "명령이 실행되는가"로만 한다(which/dpkg 같은
배포판 도구에 기대지 않는다). 설치 안내는 데비안 계열(라즈베리파이 OS,
우분투) 기준이며, 그 외 배포판이면 안내 문구만 다를 뿐 판정은 동일하다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PROBE_TIMEOUT = 8.0


@dataclass
class PrereqResult:
    key: str
    label: str
    ok: bool
    detail: str = ""          # 버전 문자열 또는 실패 사유
    why: str = ""             # 이게 없으면 무엇을 못 하는지
    install: list[str] = field(default_factory=list)  # 설치 명령(여러 줄)
    warning: str = ""         # 설치는 됐지만 남은 준비물(예: 로그인 안 됨)


CLOUDFLARED_INSTALL = [
    "curl -L -o cloudflared.deb \\",
    "  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb",
    "sudo dpkg -i cloudflared.deb && rm cloudflared.deb",
]
GIT_INSTALL = ["sudo apt update && sudo apt install -y git"]
DOCKER_INSTALL = [
    "curl -fsSL https://get.docker.com | sudo sh",
    "sudo usermod -aG docker $USER      # 다시 로그인해야 적용됩니다",
]

WHY = {
    "cloudflared": "터널을 만들고 실행하는 프로그램입니다. 없으면 이 기기에서 "
                   "터널을 켤 수 없습니다.",
    "git": "프로젝트를 내려받고 업데이트할 때 씁니다. 없으면 클론이 실패합니다.",
    "docker": "서비스 종류를 '도커 컴포즈'로 등록한 서버를 켤 때 씁니다. "
              "명령으로만 서버를 돌린다면 없어도 됩니다.",
}


def _probe(runner, argv: list[str]) -> tuple[bool, str]:
    """명령을 실행해 (성공 여부, 첫 줄)을 돌려준다."""
    try:
        res = runner.run(argv, timeout=PROBE_TIMEOUT)
    except Exception as ex:
        return False, str(ex)
    if res.exit_code != 0:
        text = (res.stderr or res.stdout).strip().splitlines()
        return False, (text[0] if text else f"종료 코드 {res.exit_code}")
    out = (res.stdout or res.stderr).strip().splitlines()
    return True, (out[0] if out else "")


def check_prereqs(runner, cloudflared_binary: str = "cloudflared") -> list[PrereqResult]:
    """대상 기기의 준비물을 점검한다. 순서는 화면에 보여줄 순서와 같다."""
    results: list[PrereqResult] = []

    ok, detail = _probe(runner, [cloudflared_binary, "--version"])
    warning = ""
    if ok:
        # 설치돼 있어도 로그인(cert.pem)이 없으면 터널을 만들 수 없다.
        try:
            home = runner.home_dir().replace("\\", "/")
            if not runner.file_exists(f"{home}/.cloudflared/cert.pem"):
                warning = ("로그인 기록(cert.pem)이 없습니다. 이 기기에서 "
                           "`cloudflared tunnel login`을 한 번 실행하세요.")
        except Exception:
            pass  # 확인 실패는 경고 없이 넘어간다(설치 여부 판정과 무관)
    results.append(PrereqResult(
        key="cloudflared", label="cloudflared", ok=ok, detail=detail,
        why=WHY["cloudflared"], install=CLOUDFLARED_INSTALL, warning=warning))

    ok, detail = _probe(runner, ["git", "--version"])
    results.append(PrereqResult(
        key="git", label="git", ok=ok, detail=detail,
        why=WHY["git"], install=GIT_INSTALL))

    ok, detail = _probe(runner, ["docker", "--version"])
    warning = ""
    if ok:
        # docker는 있는데 compose 플러그인이 없는 조합이 흔하다(구버전 설치).
        compose_ok, _ = _probe(runner, ["docker", "compose", "version"])
        if not compose_ok:
            warning = ("docker compose 플러그인이 없습니다. "
                       "sudo apt install -y docker-compose-plugin")
    results.append(PrereqResult(
        key="docker", label="docker", ok=ok, detail=detail,
        why=WHY["docker"], install=DOCKER_INSTALL, warning=warning))

    return results


def all_ok(results: list[PrereqResult]) -> bool:
    return all(r.ok for r in results)


def has_warning(results: list[PrereqResult]) -> bool:
    return any(r.warning for r in results)


def summary(results: list[PrereqResult]) -> str:
    """모달 제목 아래에 쓸 한 줄 요약."""
    missing = [r.label for r in results if not r.ok]
    if missing:
        return f"{', '.join(missing)} 이(가) 없습니다. 아래 명령으로 설치하세요."
    if has_warning(results):
        return "설치는 되어 있지만 마무리할 준비물이 있습니다."
    return "필요한 프로그램이 모두 준비되어 있습니다."
