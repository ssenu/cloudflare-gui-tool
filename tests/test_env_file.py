from app.core.env_file import (EnvFile, compose_host_port, is_secret_key,
                               validate_env_key, validate_port)


# ---- 파싱/보존 ----

def test_items_reads_pairs_and_skips_comments():
    env = EnvFile("# 주석\n\nA=1\nB=hello\n")
    assert env.items() == [("A", "1"), ("B", "hello")]


def test_set_preserves_comments_and_order():
    text = "# 관리자 비밀번호\nADMIN_PASSWORD=old\n\n# 포트\nHOST_PORT=8000\n"
    env = EnvFile(text)
    env.set("HOST_PORT", "8001")
    assert env.to_text() == (
        "# 관리자 비밀번호\nADMIN_PASSWORD=old\n\n# 포트\nHOST_PORT=8001\n")


def test_set_appends_when_missing():
    env = EnvFile("A=1\n")
    env.set("B", "2")
    assert env.to_text() == "A=1\nB=2\n"


def test_set_on_empty_file():
    env = EnvFile()
    env.set("HOST_PORT", "8002")
    assert env.to_text() == "HOST_PORT=8002\n"


def test_duplicate_key_last_wins_and_set_updates_last():
    env = EnvFile("A=1\nA=2\n")
    assert env.get("A") == "2"
    env.set("A", "3")
    assert env.to_text() == "A=1\nA=3\n"  # 앞줄은 건드리지 않는다


def test_quotes_are_stripped_on_read():
    env = EnvFile('A="hello world"\nB=\'x\'\n')
    assert env.get("A") == "hello world"
    assert env.get("B") == "x"


def test_value_with_space_or_hash_is_quoted_on_write():
    env = EnvFile()
    env.set("A", "hello world")
    env.set("B", "pw#1")
    env.set("C", "plain")
    assert 'A="hello world"' in env.to_text()
    assert 'B="pw#1"' in env.to_text()
    assert "C=plain" in env.to_text()  # 필요 없으면 따옴표를 붙이지 않는다


def test_unknown_lines_are_preserved():
    # 파싱하지 못하는 줄도 그대로 남겨야 한다 - 앱이 모르는 문법이라고
    # 사용자의 파일을 지워버리면 안 된다.
    env = EnvFile("이상한 줄\nA=1\n")
    env.set("A", "2")
    assert env.to_text() == "이상한 줄\nA=2\n"


def test_remove_deletes_only_definition_lines():
    env = EnvFile("# A 설명\nA=1\nB=2\n")
    env.remove("A")
    assert env.to_text() == "# A 설명\nB=2\n"


def test_empty_file_round_trips_to_empty():
    assert EnvFile("").to_text() == ""


class _FakeRunner:
    def __init__(self, files):
        self.files = files

    def file_exists(self, path):
        return path in self.files

    def read_file(self, path):
        return self.files[path]


def test_load_missing_file_gives_empty():
    env = EnvFile.load(_FakeRunner({}), "/srv/apps/x/.env")
    assert env.items() == []


def test_load_reads_existing():
    env = EnvFile.load(_FakeRunner({"/x/.env": "A=1\n"}), "/x/.env")
    assert env.get("A") == "1"


# ---- 검증 ----

def test_validate_env_key():
    assert validate_env_key("HOST_PORT") == ""
    assert validate_env_key("_A1") == ""
    assert "숫자로 시작" in validate_env_key("1A")
    assert validate_env_key("A-B") != ""
    assert validate_env_key("") != ""


def test_validate_port_range():
    assert validate_port("8000") == ""
    assert validate_port("1024") == ""
    assert "1024 이상" in validate_port("80")
    assert "65535" in validate_port("70000")
    assert "숫자만" in validate_port("80a")
    assert validate_port("") != ""


def test_is_secret_key():
    assert is_secret_key("ADMIN_PASSWORD")
    assert is_secret_key("gate_password")
    assert is_secret_key("API_KEY")
    assert not is_secret_key("HOST_PORT")


# ---- compose에서 포트 추출 ----

def test_compose_host_port_plain():
    assert compose_host_port('    ports:\n      - "127.0.0.1:8001:8000"\n') == "8001"


def test_compose_host_port_two_parts():
    assert compose_host_port('      - "8080:80"\n') == "8080"


def test_compose_host_port_from_variable_default():
    text = '      - "127.0.0.1:${HOST_PORT:-8000}:8000"\n'
    assert compose_host_port(text) == "8000"


def test_compose_host_port_none_found():
    assert compose_host_port("services:\n  app:\n    build: .\n") == ""
