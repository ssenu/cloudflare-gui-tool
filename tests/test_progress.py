"""상단 한 줄 진행 표시의 문구 생성.

화면 없이 검증할 수 있게 문구 생성만 순수 함수로 떼어 둔다. "지금 무슨 일을
하는 중인지"가 사용자에게 보이는 전부라, 단계가 빠지거나 순서가 어긋나면
바로 티가 난다.
"""
import pytest

from app.core.progress import (DEPLOY_STEPS, deploy_text, service_text,
                               tunnel_text)


# ---- 배포 ----

def test_deploy_steps_are_ordered_and_numbered():
    """네 단계가 순서대로, 몇 번째인지 보이게 나와야 한다."""
    texts = [deploy_text("portfolio", step) for step in DEPLOY_STEPS]
    assert [t[:5] for t in texts] == ["[1/4]", "[2/4]", "[3/4]", "[4/4]"]
    assert all("portfolio" in t for t in texts)


def test_deploy_pull_step_says_what_it_is_doing():
    text = deploy_text("portfolio", "pull")
    assert "최신 코드" in text and "git pull" in text


def test_deploy_restart_wording_differs_by_kind():
    """도커는 '컨테이너', 일반 명령은 '서버'라고 불러야 헷갈리지 않는다."""
    assert "컨테이너" in deploy_text("api", "restart", kind="docker")
    assert "서버" in deploy_text("api", "restart", kind="command")


def test_deploy_wait_step_explains_the_wait():
    assert "응답" in deploy_text("api", "wait")


def test_deploy_done_is_a_completion_message():
    assert "완료" in deploy_text("api", "done")


def test_deploy_failure_carries_the_reason():
    text = deploy_text("api", "fail", reason="git pull 거부됨")
    assert "실패" in text and "git pull 거부됨" in text


def test_unknown_deploy_step_raises():
    """오타로 조용히 빈 배너가 뜨는 것보다 터지는 편이 낫다."""
    with pytest.raises(ValueError):
        deploy_text("api", "없는단계")


# ---- 터널 ----

def test_tunnel_start_and_done():
    assert "켜는 중" in tunnel_text("my-tunnel", "start")
    assert "켜졌" in tunnel_text("my-tunnel", "done")
    assert "my-tunnel" in tunnel_text("my-tunnel", "start")


def test_tunnel_stop_and_stopped():
    assert "끄는 중" in tunnel_text("my-tunnel", "stop")
    assert "껐" in tunnel_text("my-tunnel", "stopped")


# ---- 서버 ----

def test_service_start_waits_then_completes():
    assert "켜는 중" in service_text("api", "start")
    assert "응답" in service_text("api", "wait")
    assert "켜졌" in service_text("api", "done")


def test_service_stop():
    assert "끄는 중" in service_text("api", "stop")
    assert "껐" in service_text("api", "stopped")


def test_every_text_is_a_single_line():
    """배너는 한 줄이다. 줄바꿈이 들어가면 레이아웃이 흔들린다."""
    texts = [deploy_text("a", s) for s in DEPLOY_STEPS]
    texts += [deploy_text("a", "fail", reason="x")]
    texts += [tunnel_text("t", s) for s in ("start", "done", "stop", "stopped")]
    texts += [service_text("s", s) for s in ("start", "wait", "done", "stop", "stopped")]
    assert all("\n" not in t for t in texts)
