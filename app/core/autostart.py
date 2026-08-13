"""부팅 시 자동 실행(systemd 사용자 유닛).

왜 필요한가: 이 앱은 프로세스를 직접 띄우고 PID 파일로 추적한다. 그 방식은
앱을 꺼도 살아남지만 **대상 기기가 재부팅되면 아무도 다시 켜주지 않는다.**
도커 컨테이너는 `restart: unless-stopped`가 살려내는데 터널은 그렇지 않아,
정전 한 번에 사이트만 조용히 죽어 있는 상태가 된다.

그래서 터널을 systemd에 맡길 수 있게 한다. 선택한 이유:

- **사용자 유닛(--user)**: sudo가 필요 없다. 시스템 유닛은 /etc/systemd에
  써야 해서 비밀번호를 물어야 하는데, 이 앱은 SSH 키 인증만 쓴다.
- **linger**: 사용자 유닛은 기본적으로 로그인 세션이 끝나면 함께 멈춘다.
  `loginctl enable-linger`를 켜야 부팅 직후부터 세션 없이 돌아간다. 이것도
  자기 자신에 대해서는 sudo 없이 된다(라즈베리파이 OS/우분투에서 확인).

자동 실행을 켠 터널은 상태 판정도 PID 파일이 아니라 systemd에 묻는다 -
그러지 않으면 부팅 후 systemd가 띄운 프로세스를 앱이 못 보고 "중지됨"으로
표시해, 사용자가 토글을 켜면 같은 터널이 두 번 뜬다.
"""
from __future__ import annotations

import re

UNIT_PREFIX = "cft-tunnel-"
UNIT_DIR = "~/.config/systemd/user"

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def unit_name(tunnel: str) -> str:
    """터널 이름에서 systemd 유닛 이름을 만든다."""
    return f"{UNIT_PREFIX}{_SAFE.sub('-', tunnel)}.service"


def unit_path(unit: str) -> str:
    return f"{UNIT_DIR}/{unit}"


def build_unit(tunnel: str, exec_start: str) -> str:
    """터널용 systemd 유닛 파일 내용.

    Restart=always로 두어 프로세스가 죽어도 systemd가 다시 띄운다 - 앱이
    떠 있지 않아도 복구되는 것이 이 기능의 핵심이다.
    """
    return (
        "[Unit]\n"
        f"Description=Cloudflare Tunnel ({tunnel}) - Cloudflare Tunnel GUI\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        f"ExecStart={exec_start}\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def quote_exec(argv: list[str]) -> str:
    """systemd ExecStart 한 줄로 조립한다(공백이 있는 인자는 따옴표로)."""
    parts = []
    for a in argv:
        parts.append(f'"{a}"' if (" " in a or not a) else a)
    return " ".join(parts)


class AutostartError(Exception):
    """유닛 등록/해제 실패. 메시지는 사용자에게 그대로 보여준다."""


def _systemctl(runner, args: list[str], timeout: float = 20.0):
    return runner.run(["systemctl", "--user"] + args, timeout=timeout)


def enable(runner, tunnel: str, argv: list[str]) -> None:
    """유닛을 만들어 등록하고 지금 바로 실행한다.

    이미 앱이 띄워 둔 프로세스가 있으면 호출측이 먼저 정리해야 한다
    (그러지 않으면 같은 터널이 두 번 뜬다).
    """
    unit = unit_name(tunnel)
    runner.ensure_dir(UNIT_DIR)
    runner.write_file(unit_path(unit), build_unit(tunnel, quote_exec(argv)))

    # 세션이 끝나도 계속 돌게 한다. 실패해도 등록 자체는 진행한다 -
    # 그 경우 "로그아웃하면 멈춘다"는 점만 호출측이 안내하면 된다.
    try:
        runner.run(["loginctl", "enable-linger"], timeout=20.0)
    except Exception:
        pass

    res = _systemctl(runner, ["daemon-reload"])
    if res.exit_code != 0:
        raise AutostartError(res.stderr.strip() or "daemon-reload 실패")
    res = _systemctl(runner, ["enable", "--now", unit])
    if res.exit_code != 0:
        raise AutostartError(res.stderr.strip() or res.stdout.strip()
                             or "유닛 등록 실패")


def disable(runner, tunnel: str) -> None:
    """등록을 해제하고 유닛 파일을 지운다. 실행 중이면 함께 멈춘다."""
    unit = unit_name(tunnel)
    _systemctl(runner, ["disable", "--now", unit])
    try:
        path = unit_path(unit)
        if runner.file_exists(path):
            runner.remove_file(path)
    except Exception:
        pass
    _systemctl(runner, ["daemon-reload"])


def is_supported(runner) -> bool:
    """이 대상에서 사용자 유닛을 쓸 수 있는지(systemd 여부)."""
    try:
        res = _systemctl(runner, ["is-system-running"], timeout=10.0)
    except Exception:
        return False
    # degraded/starting 등도 systemd가 도는 상태다. 명령 자체가 되면 지원으로 본다.
    return bool((res.stdout or res.stderr).strip())


def linger_enabled(runner) -> bool:
    try:
        res = runner.run(["loginctl", "show-user", "--property=Linger"], timeout=10.0)
    except Exception:
        return False
    return "Linger=yes" in (res.stdout or "")


def active_units(runner, units: list[str]) -> set[str]:
    """여러 유닛의 활성 여부를 한 번의 호출로 확인한다.

    systemctl is-active는 유닛 순서대로 한 줄씩 상태를 출력한다.
    """
    if not units:
        return set()
    try:
        res = runner.run(["systemctl", "--user", "is-active"] + units, timeout=20.0)
    except Exception:
        return set()
    lines = (res.stdout or "").splitlines()
    alive = set()
    for unit, state in zip(units, lines):
        if state.strip() == "active":
            alive.add(unit)
    return alive


def start(runner, tunnel: str) -> None:
    res = _systemctl(runner, ["start", unit_name(tunnel)])
    if res.exit_code != 0:
        raise AutostartError(res.stderr.strip() or "시작 실패")


def stop(runner, tunnel: str) -> None:
    res = _systemctl(runner, ["stop", unit_name(tunnel)])
    if res.exit_code != 0:
        raise AutostartError(res.stderr.strip() or "정지 실패")
