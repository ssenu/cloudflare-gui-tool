from app.core.wizard_logic import (plan_steps, validate_name,
                                   validate_service, validate_subdomain)


def test_validate_name():
    assert validate_name("mysite", []) == ""
    assert "입력" in validate_name("", [])
    assert "존재" in validate_name("dup", ["dup"])
    assert validate_name("한글불가!", []) != ""


def test_validate_subdomain():
    assert validate_subdomain("mysite") == ""
    assert validate_subdomain("my-site") == ""
    assert validate_subdomain("") != ""
    assert validate_subdomain("bad_sub!") != ""


def test_validate_service():
    assert validate_service("http://localhost:8000") == ""
    assert validate_service("https://127.0.0.1:8443") == ""
    assert validate_service("localhost:8000") != ""  # 스킴 필수
    assert validate_service("") != ""


def test_plan_steps_previews_commands():
    steps = plan_steps("mysite", "mysite.example.com", "http://localhost:8000")
    assert len(steps) == 3
    assert "cloudflared tunnel create mysite" in steps[0].preview
    assert "route dns mysite mysite.example.com" in steps[1].preview
    assert "config-mysite.yml" in steps[2].preview
