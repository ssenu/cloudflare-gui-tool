"""상단 배너에 띄우는 '지금 무슨 일을 하는 중인지' 한 줄 문구.

배너는 한 줄이고, 그 한 줄이 매 순간 바뀐다. 예전에는 "배포 중입니다...
완료되면 알려드립니다" 하나로 끝나서, 몇 분씩 걸리는 동안 어디까지 갔는지
알 수 없었다 - git pull에서 막힌 것인지, 이미지 빌드 중인지, 다 떴는데
아직 응답을 못 받은 것인지가 전부 같은 문구였다.

문구 생성만 여기 모아 둔 이유는 화면 없이 검증하기 위해서다. 어느 단계에서
무엇을 보여줄지가 이 기능의 전부라, 단계가 빠지거나 순서가 어긋나는 것을
테스트로 잡을 수 있어야 한다.
"""

from __future__ import annotations

# 배포의 네 단계. 순서가 곧 사용자에게 보이는 번호([1/4] ...)다.
DEPLOY_STEPS = ("pull", "restart", "wait", "done")

_DEPLOY_LABELS = {
    "pull": "최신 코드 받는 중 (git pull)...",
    "restart": None,        # 종류에 따라 달라진다(아래 참고)
    "wait": "웹이 열리기를 기다리는 중...",
    "done": "배포 완료!",
}

_TUNNEL_LABELS = {
    "start": "터널 켜는 중...",
    "done": "터널이 켜졌습니다.",
    "stop": "터널 끄는 중...",
    "stopped": "터널을 껐습니다.",
    "restart": "터널 다시 켜는 중...",
}

_SERVICE_LABELS = {
    "start": "서버 켜는 중...",
    "wait": "웹이 열리기를 기다리는 중...",
    "done": "서버가 켜졌습니다.",
    "stop": "서버 끄는 중...",
    "stopped": "서버를 껐습니다.",
}


def _line(name: str, tail: str) -> str:
    """'이름 · 하는 일' 한 줄. 이름이 없으면 하는 일만."""
    return f"{name} · {tail}" if name else tail


def deploy_text(label: str, step: str, kind: str = "docker",
                reason: str = "") -> str:
    """배포 진행 한 줄.

    step은 DEPLOY_STEPS 중 하나이거나 "fail"이다. 모르는 단계는 조용히 빈
    배너를 띄우는 대신 예외로 알린다 - 오타가 나면 사용자에게는 "아무 일도
    안 일어나는" 것으로 보이기 때문이다.
    """
    if step == "fail":
        return _line(label, f"배포 실패: {reason}" if reason else "배포 실패")
    if step not in _DEPLOY_LABELS:
        raise ValueError(f"모르는 배포 단계: {step}")
    if step == "restart":
        # 도커는 '컨테이너', 그냥 명령으로 띄우는 서버는 '서버'라고 부른다.
        what = "컨테이너" if kind == "docker" else "서버"
        tail = f"{what} 다시 시작하는 중..."
    else:
        tail = _DEPLOY_LABELS[step]
    index = DEPLOY_STEPS.index(step) + 1
    return f"[{index}/{len(DEPLOY_STEPS)}] {_line(label, tail)}"


def tunnel_text(name: str, step: str) -> str:
    """터널 켜기/끄기 진행 한 줄."""
    if step not in _TUNNEL_LABELS:
        raise ValueError(f"모르는 터널 단계: {step}")
    return _line(name, _TUNNEL_LABELS[step])


def service_text(label: str, step: str) -> str:
    """서버 켜기/끄기 진행 한 줄."""
    if step not in _SERVICE_LABELS:
        raise ValueError(f"모르는 서버 단계: {step}")
    return _line(label, _SERVICE_LABELS[step])
