# Cloudflare Tunnel GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** cloudflared 터널 생성/목록/on-off/웹서버 실행/SSH 원격 제어를 GUI로 수행하는 PyQt6 데스크톱 앱.

**Architecture:** core(비-Qt, 순수 파이썬: Runner 추상화 → cloudflared 래퍼 → 프로세스 관리자)와 ui(PyQt6)를 분리. UI는 QTimer 폴링으로 core의 상태/로그 버퍼를 읽는다 (크로스스레드 시그널 문제 회피). 로컬은 subprocess, 원격은 paramiko SSH로 동일 인터페이스 구현.

**Tech Stack:** Python 3.11+, PyQt6, PyYAML, paramiko, pytest

**스펙:** `docs/superpowers/specs/2026-08-10-cloudflare-tunnel-gui-design.md`

## Global Constraints

- 대상 OS: Windows 11 (로컬), Raspberry Pi OS (원격)
- cloudflared 명령은 반드시 `CommandRunner`를 경유한다 (UI에서 subprocess 직접 호출 금지)
- core 모듈(`app/core/*`)은 PyQt6를 import하지 않는다
- 앱 설정 저장 위치: `%APPDATA%/CloudflareTunnelGUI/settings.json`
- 터널별 config: `~/.cloudflared/config-<터널이름>.yml`
- SSH 비밀번호는 디스크에 저장하지 않는다 (키 파일 경로만 저장)
- UI 텍스트는 한국어
- 다크 테마, 외부 테마 라이브러리 의존 금지 (커스텀 QSS)
- 스펙 대비 확정 단순화: ① 루트 도메인은 cert.pem 파싱 대신 최초 1회 수동 입력 후 settings.json에 저장·자동완성 ② 원격 상태 감지는 pgrep 폴링 대신 SSH 채널 생존 여부로 판정

---

### Task 1: 프로젝트 스캐폴딩 + 설정 스토어 (store.py)

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `app/__init__.py`, `app/core/__init__.py`, `app/ui/__init__.py`, `tests/__init__.py`
- Create: `app/core/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `TunnelMeta(name, hostname, service, server_cmd, server_cwd, start_together)` dataclass, `SshProfile(name, host, port, username, key_path)` dataclass, `Settings(root_domain, cloudflared_path, tunnels: dict[str, TunnelMeta], ssh_profiles: list[SshProfile])`, `SettingsStore(path=None)` with `.load() -> Settings`, `.save()`, `.settings` attribute, `default_settings_path() -> str`

- [ ] **Step 1: 스캐폴딩 파일 작성**

`requirements.txt`:
```
PyQt6>=6.6
PyYAML>=6.0
paramiko>=3.4
pytest>=8.0
```

`pyproject.toml`:
```toml
[project]
name = "cloudflare-tunnel-gui"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

빈 `__init__.py` 4개 생성. 그 후 `pip install -r requirements.txt` 실행.

- [ ] **Step 2: 실패하는 테스트 작성** (`tests/test_store.py`)

```python
import json
from app.core.store import SettingsStore, TunnelMeta, SshProfile


def test_load_missing_file_returns_defaults(tmp_path):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    s = store.load()
    assert s.root_domain == ""
    assert s.tunnels == {}
    assert s.ssh_profiles == []


def test_save_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "settings.json")
    store = SettingsStore(path=path)
    store.load()
    store.settings.root_domain = "example.com"
    store.settings.tunnels["mysite"] = TunnelMeta(
        name="mysite", hostname="mysite.example.com",
        service="http://localhost:8000",
        server_cmd="uvicorn main:app --port 8000",
        server_cwd="C:/proj", start_together=True)
    store.settings.ssh_profiles.append(
        SshProfile(name="rpi", host="192.168.0.10", port=22,
                   username="pi", key_path="C:/keys/id_ed25519"))
    store.save()

    loaded = SettingsStore(path=path).load()
    assert loaded.root_domain == "example.com"
    assert loaded.tunnels["mysite"].start_together is True
    assert loaded.ssh_profiles[0].host == "192.168.0.10"
    # 파일이 사람이 읽을 수 있는 JSON인지
    raw = json.loads(open(path, encoding="utf-8").read())
    assert "tunnels" in raw
```

- [ ] **Step 3: 실패 확인**

Run: `pytest tests/test_store.py -v` → Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 4: 구현** (`app/core/store.py`)

```python
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field


@dataclass
class TunnelMeta:
    name: str
    hostname: str = ""
    service: str = ""
    server_cmd: str = ""
    server_cwd: str = ""
    start_together: bool = False


@dataclass
class SshProfile:
    name: str
    host: str
    port: int = 22
    username: str = "pi"
    key_path: str = ""


@dataclass
class Settings:
    root_domain: str = ""
    cloudflared_path: str = ""
    tunnels: dict[str, TunnelMeta] = field(default_factory=dict)
    ssh_profiles: list[SshProfile] = field(default_factory=list)


def default_settings_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "CloudflareTunnelGUI", "settings.json")


class SettingsStore:
    def __init__(self, path: str | None = None):
        self.path = path or default_settings_path()
        self.settings = Settings()

    def load(self) -> Settings:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            self.settings = Settings(
                root_domain=raw.get("root_domain", ""),
                cloudflared_path=raw.get("cloudflared_path", ""),
                tunnels={k: TunnelMeta(**v) for k, v in raw.get("tunnels", {}).items()},
                ssh_profiles=[SshProfile(**p) for p in raw.get("ssh_profiles", [])],
            )
        return self.settings

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        raw = {
            "root_domain": self.settings.root_domain,
            "cloudflared_path": self.settings.cloudflared_path,
            "tunnels": {k: asdict(v) for k, v in self.settings.tunnels.items()},
            "ssh_profiles": [asdict(p) for p in self.settings.ssh_profiles],
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
```

- [ ] **Step 5: 통과 확인** — `pytest tests/test_store.py -v` → PASS

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: 프로젝트 스캐폴딩 + 설정 스토어"`

---

### Task 2: CommandRunner 추상화 + LocalRunner (runner.py)

**Files:**
- Create: `app/core/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces:
  - `RunResult(exit_code: int, stdout: str, stderr: str)` dataclass
  - `ManagedProcess` (ABC): `.is_running() -> bool`, `.stop(timeout: float = 5.0)`
  - `CommandRunner` (ABC): `.name: str`, `.run(cmd: list[str], timeout: float = 60.0) -> RunResult`, `.spawn(cmd: list[str], cwd: str | None = None, on_line: Callable[[str, str], None] | None = None, on_exit: Callable[[int], None] | None = None) -> ManagedProcess`, `.read_file(path) -> str`, `.write_file(path, text)`, `.file_exists(path) -> bool`, `.remove_file(path)`, `.home_dir() -> str`
  - `LocalRunner(CommandRunner)` — subprocess 기반. `on_line(stream, line)`의 stream은 `"stdout"` 또는 `"stderr"`
  - `shell=False`, Windows에서 콘솔창 안 뜨게 `CREATE_NO_WINDOW` 플래그

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_runner.py`)

```python
import sys
import time
from app.core.runner import LocalRunner


def test_run_captures_stdout_and_exit_code():
    r = LocalRunner()
    res = r.run([sys.executable, "-c", "print('hello')"])
    assert res.exit_code == 0
    assert "hello" in res.stdout


def test_run_captures_stderr_nonzero():
    r = LocalRunner()
    res = r.run([sys.executable, "-c", "import sys; sys.stderr.write('bad'); sys.exit(3)"])
    assert res.exit_code == 3
    assert "bad" in res.stderr


def test_spawn_streams_lines_and_exit():
    r = LocalRunner()
    lines, exits = [], []
    p = r.spawn(
        [sys.executable, "-u", "-c", "print('l1'); print('l2')"],
        on_line=lambda s, l: lines.append((s, l)),
        on_exit=lambda c: exits.append(c))
    for _ in range(100):
        if exits:
            break
        time.sleep(0.05)
    assert exits == [0]
    assert ("stdout", "l1") in lines and ("stdout", "l2") in lines
    assert not p.is_running()


def test_spawn_stop_terminates():
    r = LocalRunner()
    p = r.spawn([sys.executable, "-c", "import time; time.sleep(60)"])
    assert p.is_running()
    p.stop()
    assert not p.is_running()


def test_file_ops(tmp_path):
    r = LocalRunner()
    f = str(tmp_path / "a.txt")
    assert not r.file_exists(f)
    r.write_file(f, "내용")
    assert r.file_exists(f)
    assert r.read_file(f) == "내용"
    r.remove_file(f)
    assert not r.file_exists(f)
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_runner.py -v` → FAIL

- [ ] **Step 3: 구현** (`app/core/runner.py`)

```python
from __future__ import annotations

import os
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

OnLine = Callable[[str, str], None]  # (stream_name: "stdout"|"stderr", line)
OnExit = Callable[[int], None]

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


class ManagedProcess(ABC):
    @abstractmethod
    def is_running(self) -> bool: ...

    @abstractmethod
    def stop(self, timeout: float = 5.0) -> None: ...


class CommandRunner(ABC):
    name: str = "local"

    @abstractmethod
    def run(self, cmd: list[str], timeout: float = 60.0) -> RunResult: ...

    @abstractmethod
    def spawn(self, cmd: list[str], cwd: str | None = None,
              on_line: OnLine | None = None,
              on_exit: OnExit | None = None) -> ManagedProcess: ...

    @abstractmethod
    def read_file(self, path: str) -> str: ...

    @abstractmethod
    def write_file(self, path: str, text: str) -> None: ...

    @abstractmethod
    def file_exists(self, path: str) -> bool: ...

    @abstractmethod
    def remove_file(self, path: str) -> None: ...

    @abstractmethod
    def home_dir(self) -> str: ...


class LocalProcess(ManagedProcess):
    def __init__(self, popen: subprocess.Popen,
                 on_line: OnLine | None, on_exit: OnExit | None):
        self._p = popen
        self._on_exit = on_exit
        self._threads = []
        for stream_name in ("stdout", "stderr"):
            pipe = getattr(popen, stream_name)
            t = threading.Thread(target=self._pump, args=(stream_name, pipe, on_line),
                                 daemon=True)
            t.start()
            self._threads.append(t)
        threading.Thread(target=self._wait, daemon=True).start()

    def _pump(self, name: str, pipe, on_line: OnLine | None):
        for raw in iter(pipe.readline, ""):
            if on_line:
                on_line(name, raw.rstrip("\r\n"))
        pipe.close()

    def _wait(self):
        code = self._p.wait()
        for t in self._threads:
            t.join(timeout=2)
        if self._on_exit:
            self._on_exit(code)

    def is_running(self) -> bool:
        return self._p.poll() is None

    def stop(self, timeout: float = 5.0) -> None:
        if not self.is_running():
            return
        self._p.terminate()
        try:
            self._p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._p.kill()
            self._p.wait(timeout=timeout)


class LocalRunner(CommandRunner):
    name = "local"

    def run(self, cmd: list[str], timeout: float = 60.0) -> RunResult:
        res = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return RunResult(res.returncode, res.stdout, res.stderr)

    def spawn(self, cmd, cwd=None, on_line=None, on_exit=None) -> ManagedProcess:
        popen = subprocess.Popen(
            cmd, cwd=cwd or None, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=CREATE_NO_WINDOW)
        return LocalProcess(popen, on_line, on_exit)

    def read_file(self, path: str) -> str:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            return f.read()

    def write_file(self, path: str, text: str) -> None:
        p = os.path.expanduser(path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def file_exists(self, path: str) -> bool:
        return os.path.exists(os.path.expanduser(path))

    def remove_file(self, path: str) -> None:
        os.remove(os.path.expanduser(path))

    def home_dir(self) -> str:
        return os.path.expanduser("~")
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_runner.py -v` → PASS

- [ ] **Step 5: Commit** — `git commit -am "feat: CommandRunner 추상화 + LocalRunner"` (신규 파일 add 포함)

---

### Task 3: config.yml 생성/파싱 (config_yml.py)

**Files:**
- Create: `app/core/config_yml.py`
- Test: `tests/test_config_yml.py`

**Interfaces:**
- Produces: `build_config(tunnel_id: str, credentials_file: str, hostname: str, service: str) -> str` (YAML 문자열), `parse_config(text: str) -> dict`, `get_main_ingress(cfg: dict) -> tuple[str, str]` ((hostname, service), 없으면 ("","")), `update_service(text: str, service: str) -> str` (첫 ingress의 service만 교체한 YAML 반환)

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_config_yml.py`)

```python
from app.core.config_yml import (build_config, get_main_ingress,
                                 parse_config, update_service)

TID = "a1b2c3d4-1111-2222-3333-444455556666"
CRED = "C:/Users/me/.cloudflared/a1b2c3d4.json"


def test_build_and_parse_roundtrip():
    text = build_config(TID, CRED, "mysite.example.com", "http://localhost:8000")
    cfg = parse_config(text)
    assert cfg["tunnel"] == TID
    assert cfg["credentials-file"] == CRED
    assert cfg["ingress"][0] == {"hostname": "mysite.example.com",
                                 "service": "http://localhost:8000"}
    assert cfg["ingress"][-1] == {"service": "http_status:404"}  # fallback 필수


def test_get_main_ingress():
    text = build_config(TID, CRED, "a.example.com", "http://localhost:9000")
    assert get_main_ingress(parse_config(text)) == ("a.example.com",
                                                    "http://localhost:9000")
    assert get_main_ingress({}) == ("", "")


def test_update_service_keeps_rest():
    text = build_config(TID, CRED, "a.example.com", "http://localhost:9000")
    new = update_service(text, "http://localhost:5173")
    cfg = parse_config(new)
    assert cfg["ingress"][0]["service"] == "http://localhost:5173"
    assert cfg["ingress"][0]["hostname"] == "a.example.com"
    assert cfg["tunnel"] == TID
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_config_yml.py -v` → FAIL

- [ ] **Step 3: 구현** (`app/core/config_yml.py`)

```python
from __future__ import annotations

import yaml


def build_config(tunnel_id: str, credentials_file: str,
                 hostname: str, service: str) -> str:
    data = {
        "tunnel": tunnel_id,
        "credentials-file": credentials_file,
        "ingress": [
            {"hostname": hostname, "service": service},
            {"service": "http_status:404"},
        ],
    }
    return yaml.dump(data, sort_keys=False, allow_unicode=True)


def parse_config(text: str) -> dict:
    return yaml.safe_load(text) or {}


def get_main_ingress(cfg: dict) -> tuple[str, str]:
    for rule in cfg.get("ingress", []):
        if "hostname" in rule:
            return rule.get("hostname", ""), rule.get("service", "")
    return "", ""


def update_service(text: str, service: str) -> str:
    cfg = parse_config(text)
    for rule in cfg.get("ingress", []):
        if "hostname" in rule:
            rule["service"] = service
            break
    return yaml.dump(cfg, sort_keys=False, allow_unicode=True)
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_config_yml.py -v` → PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: config.yml 생성/파싱"`

---

### Task 4: cloudflared CLI 래퍼 (cloudflared.py)

**Files:**
- Create: `app/core/cloudflared.py`
- Test: `tests/test_cloudflared.py`

**Interfaces:**
- Consumes: `CommandRunner`, `RunResult` (Task 2)
- Produces:
  - `TunnelInfo(id: str, name: str, created_at: str, connections: int)` dataclass
  - `CloudflaredError(Exception)` — `.result: RunResult` 보유, str()은 stderr 우선
  - `CloudflaredClient(runner: CommandRunner, binary: str = "cloudflared")`:
    - `.version() -> str | None` (미설치면 None)
    - `.cert_path() -> str`, `.cert_exists() -> bool`
    - `.config_dir() -> str` (`<home>/.cloudflared`), `.config_path(name) -> str` (`config-<name>.yml`)
    - `.list_tunnels() -> list[TunnelInfo]` (`tunnel list --output json` 파싱)
    - `.create_tunnel(name) -> tuple[str, str]` ((tunnel_id, credentials_path) — stdout에서 uuid 파싱)
    - `.delete_tunnel(name)`, `.route_dns(name, hostname)`
    - `.run_args(name) -> list[str]` = `[binary, "--config", config_path, "tunnel", "run", name]`

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_cloudflared.py`)

FakeRunner로 CLI 출력을 주입한다.

```python
import pytest
from app.core.cloudflared import CloudflaredClient, CloudflaredError
from app.core.runner import CommandRunner, RunResult


class FakeRunner(CommandRunner):
    name = "fake"

    def __init__(self, responses: dict[str, RunResult]):
        self.responses = responses  # key: 첫 매칭 서브명령 문자열
        self.calls: list[list[str]] = []

    def run(self, cmd, timeout=60.0):
        self.calls.append(cmd)
        joined = " ".join(cmd)
        for key, res in self.responses.items():
            if key in joined:
                return res
        return RunResult(0, "", "")

    def spawn(self, cmd, cwd=None, on_line=None, on_exit=None):
        raise NotImplementedError

    def read_file(self, path): return ""
    def write_file(self, path, text): pass
    def file_exists(self, path): return False
    def remove_file(self, path): pass
    def home_dir(self): return "C:/Users/me"


LIST_JSON = ('[{"id":"aaa-111","name":"mysite","created_at":"2026-08-01T00:00:00Z",'
             '"connections":[{"id":"c1"},{"id":"c2"}]},'
             '{"id":"bbb-222","name":"api","created_at":"2026-08-02T00:00:00Z",'
             '"connections":[]}]')


def test_list_tunnels_parses_json():
    r = FakeRunner({"tunnel list": RunResult(0, LIST_JSON, "")})
    tunnels = CloudflaredClient(r).list_tunnels()
    assert [t.name for t in tunnels] == ["mysite", "api"]
    assert tunnels[0].connections == 2
    assert tunnels[1].connections == 0


def test_create_tunnel_parses_id_and_credentials():
    out = ("Tunnel credentials written to "
           "C:/Users/me/.cloudflared/a1b2c3d4-1111-2222-3333-444455556666.json.\n"
           "Created tunnel mysite with id a1b2c3d4-1111-2222-3333-444455556666")
    r = FakeRunner({"tunnel create": RunResult(0, out, "")})
    tid, cred = CloudflaredClient(r).create_tunnel("mysite")
    assert tid == "a1b2c3d4-1111-2222-3333-444455556666"
    assert cred.endswith(f"{tid}.json")


def test_error_raises_with_stderr():
    r = FakeRunner({"tunnel create": RunResult(1, "", "tunnel with name already exists")})
    with pytest.raises(CloudflaredError, match="already exists"):
        CloudflaredClient(r).create_tunnel("mysite")


def test_version_none_when_missing():
    class Boom(FakeRunner):
        def run(self, cmd, timeout=60.0):
            raise FileNotFoundError

    assert CloudflaredClient(Boom({})).version() is None


def test_paths_and_run_args():
    c = CloudflaredClient(FakeRunner({}))
    assert c.config_dir() == "C:/Users/me/.cloudflared"
    assert c.config_path("mysite") == "C:/Users/me/.cloudflared/config-mysite.yml"
    assert c.run_args("mysite") == [
        "cloudflared", "--config", "C:/Users/me/.cloudflared/config-mysite.yml",
        "tunnel", "run", "mysite"]


def test_route_dns_builds_command():
    r = FakeRunner({})
    CloudflaredClient(r).route_dns("mysite", "mysite.example.com")
    assert r.calls[-1] == ["cloudflared", "tunnel", "route", "dns",
                          "mysite", "mysite.example.com"]
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_cloudflared.py -v` → FAIL

- [ ] **Step 3: 구현** (`app/core/cloudflared.py`)

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.core.runner import CommandRunner, RunResult

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                     re.IGNORECASE)


@dataclass
class TunnelInfo:
    id: str
    name: str
    created_at: str
    connections: int


class CloudflaredError(Exception):
    def __init__(self, result: RunResult):
        self.result = result
        super().__init__(result.stderr.strip() or result.stdout.strip()
                         or f"exit code {result.exit_code}")


class CloudflaredClient:
    def __init__(self, runner: CommandRunner, binary: str = "cloudflared"):
        self.runner = runner
        self.binary = binary

    def _run(self, args: list[str], timeout: float = 120.0) -> RunResult:
        res = self.runner.run([self.binary] + args, timeout=timeout)
        if res.exit_code != 0:
            raise CloudflaredError(res)
        return res

    def version(self) -> str | None:
        try:
            res = self.runner.run([self.binary, "--version"], timeout=15.0)
        except (FileNotFoundError, OSError):
            return None
        return res.stdout.strip() if res.exit_code == 0 else None

    def config_dir(self) -> str:
        return self.runner.home_dir().replace("\\", "/") + "/.cloudflared"

    def cert_path(self) -> str:
        return self.config_dir() + "/cert.pem"

    def cert_exists(self) -> bool:
        return self.runner.file_exists(self.cert_path())

    def config_path(self, name: str) -> str:
        return self.config_dir() + f"/config-{name}.yml"

    def list_tunnels(self) -> list[TunnelInfo]:
        res = self._run(["tunnel", "list", "--output", "json"])
        data = json.loads(res.stdout or "[]")
        return [TunnelInfo(id=t["id"], name=t["name"],
                           created_at=t.get("created_at", ""),
                           connections=len(t.get("connections") or []))
                for t in data]

    def create_tunnel(self, name: str) -> tuple[str, str]:
        res = self._run(["tunnel", "create", name])
        text = res.stdout + res.stderr
        m = UUID_RE.search(text)
        if not m:
            raise CloudflaredError(RunResult(1, res.stdout,
                                             "터널 ID를 출력에서 찾지 못했습니다"))
        tid = m.group(0)
        return tid, self.config_dir() + f"/{tid}.json"

    def delete_tunnel(self, name: str) -> None:
        self._run(["tunnel", "delete", name])

    def route_dns(self, name: str, hostname: str) -> None:
        self._run(["tunnel", "route", "dns", name, hostname])

    def run_args(self, name: str) -> list[str]:
        return [self.binary, "--config", self.config_path(name),
                "tunnel", "run", name]

    def login_args(self) -> list[str]:
        return [self.binary, "tunnel", "login"]
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_cloudflared.py -v` → PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: cloudflared CLI 래퍼"`

---

### Task 5: 상태 추적 + 로그 버퍼 + 프로세스 관리자 (process_mgr.py)

**Files:**
- Create: `app/core/process_mgr.py`
- Test: `tests/test_process_mgr.py`

**Interfaces:**
- Consumes: `CommandRunner.spawn`, `ManagedProcess` (Task 2), `CloudflaredClient.run_args` (Task 4), `TunnelMeta` (Task 1)
- Produces:
  - `TunnelState` Enum: `STOPPED, STARTING, RUNNING, ERROR`
  - `StatusTracker()`: `.state`, `.feed(line: str)` ("Registered tunnel connection" 포함 시 RUNNING), `.mark_starting()`, `.mark_stopping()`, `.on_exit(code: int)` (사용자 중지면 STOPPED, 아니면 ERROR)
  - `LogBuffer(maxlen=2000)`: `.append(stream, line)`, `.get_since(seq: int) -> tuple[int, list[str]]` (증분 폴링용), `.clear()`
  - `ProcessManager()`:
    - `.start_tunnel(name, runner, client) -> None` / `.stop_tunnel(name)`
    - `.start_server(meta: TunnelMeta, runner) -> None` / `.stop_server(name)`
    - `.tunnel_state(name) -> TunnelState`, `.server_running(name) -> bool`
    - `.tunnel_log(name) -> LogBuffer`, `.server_log(name) -> LogBuffer`
    - `.stop_all()`, `.any_running() -> bool`

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_process_mgr.py`)

```python
import sys
import time
from app.core.process_mgr import (LogBuffer, ProcessManager, StatusTracker,
                                  TunnelState)
from app.core.runner import LocalRunner
from app.core.store import TunnelMeta


def test_tracker_transitions_to_running():
    t = StatusTracker()
    assert t.state == TunnelState.STOPPED
    t.mark_starting()
    assert t.state == TunnelState.STARTING
    t.feed("2026-08-10T00:00:00Z INF Registered tunnel connection connIndex=0")
    assert t.state == TunnelState.RUNNING


def test_tracker_user_stop_vs_crash():
    t = StatusTracker()
    t.mark_starting()
    t.mark_stopping()
    t.on_exit(1)
    assert t.state == TunnelState.STOPPED  # 사용자가 중지한 경우
    t2 = StatusTracker()
    t2.mark_starting()
    t2.on_exit(1)
    assert t2.state == TunnelState.ERROR  # 비정상 종료


def test_log_buffer_incremental():
    b = LogBuffer(maxlen=10)
    b.append("stdout", "a")
    b.append("stderr", "b")
    seq, lines = b.get_since(0)
    assert lines == ["a", "b"]
    b.append("stdout", "c")
    seq2, lines2 = b.get_since(seq)
    assert lines2 == ["c"]


def test_server_start_stop():
    mgr = ProcessManager()
    runner = LocalRunner()
    meta = TunnelMeta(name="t1",
                      server_cmd=f'"{sys.executable}" -c "import time; time.sleep(60)"')
    mgr.start_server(meta, runner)
    assert mgr.server_running("t1")
    mgr.stop_server("t1")
    time.sleep(0.3)
    assert not mgr.server_running("t1")
    mgr.stop_all()
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_process_mgr.py -v` → FAIL

- [ ] **Step 3: 구현** (`app/core/process_mgr.py`)

```python
from __future__ import annotations

import shlex
import threading
from collections import deque
from enum import Enum, auto

from app.core.cloudflared import CloudflaredClient
from app.core.runner import CommandRunner, ManagedProcess
from app.core.store import TunnelMeta

RUNNING_MARKER = "Registered tunnel connection"


class TunnelState(Enum):
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    ERROR = auto()


class StatusTracker:
    def __init__(self):
        self.state = TunnelState.STOPPED
        self._stopping = False

    def mark_starting(self):
        self.state = TunnelState.STARTING
        self._stopping = False

    def mark_stopping(self):
        self._stopping = True

    def feed(self, line: str):
        if RUNNING_MARKER in line:
            self.state = TunnelState.RUNNING

    def on_exit(self, code: int):
        if self._stopping or code == 0:
            self.state = TunnelState.STOPPED
        else:
            self.state = TunnelState.ERROR


class LogBuffer:
    def __init__(self, maxlen: int = 2000):
        self._lines: deque[tuple[int, str]] = deque(maxlen=maxlen)
        self._seq = 0
        self._lock = threading.Lock()

    def append(self, stream: str, line: str):
        with self._lock:
            self._seq += 1
            self._lines.append((self._seq, line))

    def get_since(self, seq: int) -> tuple[int, list[str]]:
        with self._lock:
            new = [l for s, l in self._lines if s > seq]
            return self._seq, new

    def clear(self):
        with self._lock:
            self._lines.clear()


class _Handle:
    def __init__(self, proc: ManagedProcess, tracker: StatusTracker, log: LogBuffer):
        self.proc = proc
        self.tracker = tracker
        self.log = log


class ProcessManager:
    def __init__(self):
        self._tunnels: dict[str, _Handle] = {}
        self._servers: dict[str, _Handle] = {}

    # ---- 터널 ----
    def start_tunnel(self, name: str, runner: CommandRunner,
                     client: CloudflaredClient) -> None:
        if name in self._tunnels and self._tunnels[name].proc.is_running():
            return
        tracker = StatusTracker()
        log = self.tunnel_log(name)
        tracker.mark_starting()

        def on_line(stream, line):
            log.append(stream, line)
            tracker.feed(line)

        proc = runner.spawn(client.run_args(name), on_line=on_line,
                            on_exit=tracker.on_exit)
        self._tunnels[name] = _Handle(proc, tracker, log)

    def stop_tunnel(self, name: str) -> None:
        h = self._tunnels.get(name)
        if h:
            h.tracker.mark_stopping()
            h.proc.stop()

    def tunnel_state(self, name: str) -> TunnelState:
        h = self._tunnels.get(name)
        if not h:
            return TunnelState.STOPPED
        if h.tracker.state == TunnelState.RUNNING and not h.proc.is_running():
            return TunnelState.ERROR
        return h.tracker.state

    def tunnel_log(self, name: str) -> LogBuffer:
        h = self._tunnels.get(name)
        return h.log if h else self._make_log("_t_" + name)

    # ---- 웹서버 ----
    def start_server(self, meta: TunnelMeta, runner: CommandRunner) -> None:
        name = meta.name
        if name in self._servers and self._servers[name].proc.is_running():
            return
        log = self.server_log(name)
        tracker = StatusTracker()
        cmd = shlex.split(meta.server_cmd, posix=False)
        # posix=False: Windows 경로 역슬래시 보존. 따옴표는 벗겨준다.
        cmd = [c.strip('"') for c in cmd]
        proc = runner.spawn(cmd, cwd=meta.server_cwd or None,
                            on_line=lambda s, l: log.append(s, l),
                            on_exit=tracker.on_exit)
        self._servers[name] = _Handle(proc, tracker, log)

    def stop_server(self, name: str) -> None:
        h = self._servers.get(name)
        if h:
            h.tracker.mark_stopping()
            h.proc.stop()

    def server_running(self, name: str) -> bool:
        h = self._servers.get(name)
        return bool(h and h.proc.is_running())

    def server_log(self, name: str) -> LogBuffer:
        h = self._servers.get(name)
        return h.log if h else self._make_log("_s_" + name)

    # ---- 공통 ----
    _log_cache: dict[str, LogBuffer] = {}

    def _make_log(self, key: str) -> LogBuffer:
        # start 전에 로그 객체를 요청해도 같은 인스턴스를 돌려주기 위한 캐시
        if key not in self._log_cache:
            self._log_cache[key] = LogBuffer()
        return self._log_cache[key]

    def stop_all(self) -> None:
        for name in list(self._tunnels):
            self.stop_tunnel(name)
        for name in list(self._servers):
            self.stop_server(name)

    def any_running(self) -> bool:
        return (any(h.proc.is_running() for h in self._tunnels.values())
                or any(h.proc.is_running() for h in self._servers.values()))
```

주의: `_log_cache`는 클래스 변수가 아니라 인스턴스 변수여야 한다 — `__init__`에서 `self._log_cache = {}`로 초기화하도록 구현할 것 (위 코드에서 해당 줄을 `__init__`로 옮긴다).

- [ ] **Step 4: 통과 확인** — `pytest tests/test_process_mgr.py -v` → PASS

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: 프로세스 관리자 + 상태 추적 + 로그 버퍼"`

---

### Task 6: SshRunner (paramiko) (ssh_runner.py)

**Files:**
- Create: `app/core/ssh_runner.py`
- Test: `tests/test_ssh_runner.py` (명령 문자열 조립 등 순수 로직만; 실제 SSH는 수동 검증)

**Interfaces:**
- Consumes: `CommandRunner`, `ManagedProcess`, `RunResult` (Task 2), `SshProfile` (Task 1)
- Produces:
  - `quote_cmd(cmd: list[str]) -> str` — POSIX 셸용 안전 인용 (shlex.quote 조합)
  - `SshRunner(profile: SshProfile, password: str | None = None)` — `.connect()` (paramiko SSHClient, 키 파일 우선, AutoAddPolicy), `.close()`, `.is_connected() -> bool`, CommandRunner 전체 구현 (run은 exec_command, spawn은 get_pty=True 채널 + 리더 스레드, 파일 조작은 SFTP, home_dir은 `sftp.normalize(".")`)
  - `.name`은 `"ssh:<profile.name>"`
  - spawn된 프로세스의 stop() = 채널 close (get_pty라 원격 프로세스에 HUP 전달됨)

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_ssh_runner.py`)

```python
from app.core.ssh_runner import quote_cmd
from app.core.store import SshProfile
from app.core.ssh_runner import SshRunner


def test_quote_cmd_spaces_and_specials():
    assert quote_cmd(["echo", "hello world"]) == "echo 'hello world'"
    assert quote_cmd(["cloudflared", "tunnel", "run", "my-tunnel"]) == \
        "cloudflared tunnel run my-tunnel"


def test_runner_name_includes_profile():
    r = SshRunner(SshProfile(name="rpi", host="1.2.3.4"))
    assert r.name == "ssh:rpi"
    assert not r.is_connected()
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_ssh_runner.py -v` → FAIL

- [ ] **Step 3: 구현** (`app/core/ssh_runner.py`)

```python
from __future__ import annotations

import shlex
import threading

import paramiko

from app.core.runner import CommandRunner, ManagedProcess, OnExit, OnLine, RunResult
from app.core.store import SshProfile


def quote_cmd(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


class SshProcess(ManagedProcess):
    def __init__(self, channel: paramiko.Channel,
                 on_line: OnLine | None, on_exit: OnExit | None):
        self._ch = channel
        self._on_exit = on_exit
        self._closed = False
        threading.Thread(target=self._pump, args=(on_line,), daemon=True).start()

    def _pump(self, on_line: OnLine | None):
        buf = ""
        while True:
            data = self._ch.recv(4096)
            if not data:
                break
            buf += data.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if on_line:
                    on_line("stdout", line.rstrip("\r"))
        code = self._ch.recv_exit_status() if not self._closed else 0
        if self._on_exit:
            self._on_exit(0 if self._closed else code)

    def is_running(self) -> bool:
        return not self._ch.closed and not self._ch.exit_status_ready()

    def stop(self, timeout: float = 5.0) -> None:
        self._closed = True
        self._ch.close()


class SshRunner(CommandRunner):
    def __init__(self, profile: SshProfile, password: str | None = None):
        self.profile = profile
        self.password = password
        self.name = f"ssh:{profile.name}"
        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None

    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict = dict(hostname=self.profile.host, port=self.profile.port,
                            username=self.profile.username, timeout=10)
        if self.profile.key_path:
            kwargs["key_filename"] = self.profile.key_path
        if self.password:
            kwargs["password"] = self.password
        client.connect(**kwargs)
        self._client = client
        self._sftp = client.open_sftp()

    def close(self) -> None:
        if self._sftp:
            self._sftp.close()
        if self._client:
            self._client.close()
        self._client = self._sftp = None

    def is_connected(self) -> bool:
        t = self._client.get_transport() if self._client else None
        return bool(t and t.is_active())

    def _require(self) -> paramiko.SSHClient:
        if not self.is_connected():
            raise ConnectionError("SSH 연결이 없습니다. 먼저 연결하세요.")
        return self._client  # type: ignore[return-value]

    def run(self, cmd: list[str], timeout: float = 60.0) -> RunResult:
        _, stdout, stderr = self._require().exec_command(quote_cmd(cmd),
                                                         timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return RunResult(stdout.channel.recv_exit_status(), out, err)

    def spawn(self, cmd, cwd=None, on_line=None, on_exit=None) -> ManagedProcess:
        transport = self._require().get_transport()
        ch = transport.open_session()
        ch.get_pty()  # 채널 close 시 원격 프로세스 종료 보장
        full = quote_cmd(cmd)
        if cwd:
            full = f"cd {shlex.quote(cwd)} && {full}"
        ch.exec_command(full)
        return SshProcess(ch, on_line, on_exit)

    def read_file(self, path: str) -> str:
        assert self._sftp
        with self._sftp.open(self._expand(path)) as f:
            return f.read().decode("utf-8")

    def write_file(self, path: str, text: str) -> None:
        assert self._sftp
        with self._sftp.open(self._expand(path), "w") as f:
            f.write(text)

    def file_exists(self, path: str) -> bool:
        assert self._sftp
        try:
            self._sftp.stat(self._expand(path))
            return True
        except FileNotFoundError:
            return False

    def remove_file(self, path: str) -> None:
        assert self._sftp
        self._sftp.remove(self._expand(path))

    def home_dir(self) -> str:
        assert self._sftp
        return self._sftp.normalize(".")

    def _expand(self, path: str) -> str:
        if path.startswith("~"):
            return self.home_dir() + path[1:]
        return path
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_ssh_runner.py -v` → PASS (전체 `pytest -q`도 확인)

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: paramiko 기반 SshRunner"`

---

### Task 7: 앱 컨텍스트 + 테마 + 진입점 (context.py, theme.py, main.py)

**Files:**
- Create: `app/context.py`, `app/ui/theme.py`, `app/main.py`

**Interfaces:**
- Consumes: `SettingsStore` (Task 1), `LocalRunner` (Task 2), `CloudflaredClient` (Task 4), `ProcessManager` (Task 5)
- Produces:
  - `AppContext`: `.store`, `.manager`, `.runner` (현재 활성 Runner), `.client` (현재 Runner 기반 CloudflaredClient), `.set_local()`, `.set_remote(profile, password=None)` (SshRunner 생성+connect), `.is_remote -> bool`
  - `theme.APP_QSS: str` (다크 테마 전체 스타일시트), `theme.STATE_COLORS: dict[TunnelState, str]`
  - `python -m app.main` 실행 시 QApplication + (Task 10의) MainWindow 표시. Task 7 시점에는 빈 QMainWindow placeholder

- [ ] **Step 1: 구현** (`app/context.py`)

```python
from __future__ import annotations

from app.core.cloudflared import CloudflaredClient
from app.core.process_mgr import ProcessManager
from app.core.runner import CommandRunner, LocalRunner
from app.core.ssh_runner import SshRunner
from app.core.store import SettingsStore, SshProfile


class AppContext:
    def __init__(self, store: SettingsStore | None = None):
        self.store = store or SettingsStore()
        self.store.load()
        self.manager = ProcessManager()
        self.local_runner = LocalRunner()
        self.runner: CommandRunner = self.local_runner
        self._remote: SshRunner | None = None

    def _binary(self) -> str:
        if not self.is_remote and self.store.settings.cloudflared_path:
            return self.store.settings.cloudflared_path
        return "cloudflared"

    @property
    def client(self) -> CloudflaredClient:
        return CloudflaredClient(self.runner, binary=self._binary())

    @property
    def is_remote(self) -> bool:
        return self.runner is not self.local_runner

    def set_local(self) -> None:
        if self._remote:
            self._remote.close()
            self._remote = None
        self.runner = self.local_runner

    def set_remote(self, profile: SshProfile, password: str | None = None) -> None:
        remote = SshRunner(profile, password=password)
        remote.connect()  # 실패 시 예외 → 호출측에서 다이얼로그 표시
        if self._remote:
            self._remote.close()
        self._remote = remote
        self.runner = remote
```

- [ ] **Step 2: 구현** (`app/ui/theme.py`)

```python
from app.core.process_mgr import TunnelState

STATE_COLORS = {
    TunnelState.STOPPED: "#888888",
    TunnelState.STARTING: "#d29922",
    TunnelState.RUNNING: "#3fb950",
    TunnelState.ERROR: "#f85149",
}

ACCENT = "#f6821f"

APP_QSS = """
QWidget { background: #0f1117; color: #e8eaf0;
          font-family: 'Segoe UI', 'Malgun Gothic'; font-size: 13px; }
QMainWindow, QDialog { background: #0f1117; }
QFrame#card { background: #181b24; border: 1px solid #2a2f3f; border-radius: 10px; }
QLabel#cardTitle { font-size: 15px; font-weight: 600; }
QLabel#cardSub { color: #9aa1b5; }
QPushButton { background: #1f2330; border: 1px solid #2a2f3f;
              border-radius: 6px; padding: 6px 14px; }
QPushButton:hover { border-color: #f6821f; }
QPushButton#primary { background: #f6821f; color: #ffffff; font-weight: 600;
                      border: none; }
QPushButton#primary:hover { background: #ff9a3d; }
QPushButton#danger { color: #f85149; }
QLineEdit, QComboBox, QSpinBox { background: #1f2330; border: 1px solid #2a2f3f;
    border-radius: 6px; padding: 6px 10px; selection-background-color: #f6821f; }
QLineEdit:focus, QComboBox:focus { border-color: #4c9aff; }
QPlainTextEdit { background: #0b0d12; border: 1px solid #2a2f3f;
    border-radius: 6px; font-family: 'Cascadia Code', Consolas; font-size: 12px; }
QScrollArea { border: none; }
QToolTip { background: #1f2330; color: #e8eaf0; border: 1px solid #2a2f3f; }
QMenu { background: #181b24; border: 1px solid #2a2f3f; }
QMenu::item:selected { background: #f6821f; color: white; }
QCheckBox::indicator { width: 16px; height: 16px; }
"""
```

- [ ] **Step 3: 구현** (`app/main.py` — placeholder 버전)

```python
from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication, QMainWindow

from app.context import AppContext
from app.ui.theme import APP_QSS


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Cloudflare Tunnel GUI")
    app.setStyleSheet(APP_QSS)
    ctx = AppContext()
    win = QMainWindow()  # Task 10에서 MainWindow(ctx)로 교체
    win.setWindowTitle("Cloudflare Tunnel GUI")
    win.resize(760, 560)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 스모크 확인**

Run: `python -m app.main` → 다크 배경의 빈 창이 뜨고 닫힌다. `pytest -q` 전체 여전히 PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: 앱 컨텍스트 + 다크 테마 + 진입점"`

---

### Task 8: 온보딩 다이얼로그 (onboarding.py)

**Files:**
- Create: `app/ui/onboarding.py`
- Modify: `app/main.py` (온보딩 필요 시 먼저 표시)

**Interfaces:**
- Consumes: `AppContext` (Task 7), `CloudflaredClient.version/cert_exists/login_args` (Task 4), `LocalRunner.spawn` (Task 2)
- Produces: `OnboardingDialog(ctx: AppContext)` (QDialog) — `needs_onboarding(ctx) -> bool` 모듈 함수 (`version() is None or not cert_exists()`)

- [ ] **Step 1: 구현** (`app/ui/onboarding.py`)

```python
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox,
                             QPushButton, QVBoxLayout)

from app.context import AppContext


def needs_onboarding(ctx: AppContext) -> bool:
    client = ctx.client
    return client.version() is None or not client.cert_exists()


class OnboardingDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("초기 설정")
        self.setMinimumWidth(460)

        lay = QVBoxLayout(self)
        title = QLabel("🌩️ Cloudflare Tunnel GUI 초기 설정")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        lay.addWidget(title)

        # 1단계: 설치
        self.install_label = QLabel()
        self.install_btn = QPushButton("winget으로 설치")
        self.install_btn.clicked.connect(self._install)
        row1 = QHBoxLayout()
        row1.addWidget(self.install_label, 1)
        row1.addWidget(self.install_btn)
        lay.addLayout(row1)

        # 2단계: 로그인
        self.login_label = QLabel()
        self.login_btn = QPushButton("Cloudflare 로그인")
        self.login_btn.clicked.connect(self._login)
        row2 = QHBoxLayout()
        row2.addWidget(self.login_label, 1)
        row2.addWidget(self.login_btn)
        lay.addLayout(row2)

        self.done_btn = QPushButton("시작하기")
        self.done_btn.setObjectName("primary")
        self.done_btn.clicked.connect(self.accept)
        lay.addWidget(self.done_btn, alignment=Qt.AlignmentFlag.AlignRight)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(2000)
        self._refresh()

    def _refresh(self):
        client = self.ctx.client
        ver = client.version()
        installed = ver is not None
        cert = installed and client.cert_exists()
        self.install_label.setText(
            f"① cloudflared 설치: {'✅ ' + ver if installed else '❌ 미설치'}")
        self.install_btn.setVisible(not installed)
        self.login_label.setText(
            f"② Cloudflare 로그인 (cert.pem): {'✅ 완료' if cert else '❌ 필요'}")
        self.login_btn.setVisible(installed and not cert)
        self.done_btn.setEnabled(installed and cert)

    def _install(self):
        self.install_btn.setEnabled(False)
        self.ctx.local_runner.spawn(
            ["winget", "install", "--id", "Cloudflare.cloudflared",
             "--accept-source-agreements", "--accept-package-agreements"],
            on_exit=lambda c: None)
        QMessageBox.information(
            self, "설치 시작",
            "설치가 진행 중입니다. 완료되면 상태가 자동으로 갱신됩니다.\n"
            "(갱신되지 않으면 앱을 재시작하세요 — PATH 반영 때문)")

    def _login(self):
        # 브라우저가 열리고 사용자가 도메인을 선택하면 cert.pem이 생성된다.
        self.ctx.local_runner.spawn(self.ctx.client.login_args())
        QMessageBox.information(
            self, "로그인", "브라우저에서 Cloudflare 로그인 후 도메인을 선택하세요.\n"
                          "완료되면 이 창의 상태가 자동으로 갱신됩니다.")
```

- [ ] **Step 2: main.py 연결** (`app/main.py`의 `main()` 수정)

```python
    ctx = AppContext()
    from app.ui.onboarding import OnboardingDialog, needs_onboarding
    if needs_onboarding(ctx):
        dlg = OnboardingDialog(ctx)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return 0
```

- [ ] **Step 3: 스모크 확인**

이미 로그인된 상태이므로: `python -m app.main` → 온보딩 없이 바로 메인 창. 온보딩 UI 확인은 `needs_onboarding`을 임시로 `True` 반환하게 바꿔 실행 → 두 단계 모두 ✅ 표시, "시작하기" 활성화 확인 후 되돌린다.

- [ ] **Step 4: Commit** — `git add -A && git commit -m "feat: 온보딩 다이얼로그"`

---

### Task 9: 터널 생성 마법사 로직 + UI (wizard.py)

**Files:**
- Create: `app/core/wizard_logic.py`, `app/ui/wizard.py`
- Test: `tests/test_wizard_logic.py`

**Interfaces:**
- Consumes: `CloudflaredClient` (Task 4), `build_config` (Task 3), `TunnelMeta`/`SettingsStore` (Task 1), `AppContext` (Task 7)
- Produces:
  - `wizard_logic.validate_name(name, existing: list[str]) -> str` (오류 메시지, 정상이면 "")
  - `wizard_logic.validate_subdomain(sub) -> str`, `wizard_logic.validate_service(url) -> str`
  - `wizard_logic.PlannedStep(label: str, preview: str)` dataclass
  - `wizard_logic.plan_steps(name, hostname, service) -> list[PlannedStep]` (실행 전 미리보기용)
  - `wizard_logic.execute_creation(client, name, hostname, service, progress: Callable[[int, str, bool], None]) -> None` — create → route dns → config 작성 순차 실행. progress(step_index, message, ok) 콜백. 실패 시 `CloudflaredError` 전파
  - `ui.wizard.TunnelWizard(ctx)` (QDialog) — 성공 시 `.created_meta: TunnelMeta` 보유

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_wizard_logic.py`)

```python
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
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_wizard_logic.py -v` → FAIL

- [ ] **Step 3: 구현** (`app/core/wizard_logic.py`)

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from app.core.cloudflared import CloudflaredClient
from app.core.config_yml import build_config

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SUB_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$")
SERVICE_RE = re.compile(r"^(https?|tcp|ssh|rdp)://\S+$")


def validate_name(name: str, existing: list[str]) -> str:
    if not name:
        return "터널 이름을 입력하세요"
    if not NAME_RE.match(name):
        return "영문, 숫자, -, _ 만 사용할 수 있습니다"
    if name in existing:
        return "같은 이름의 터널이 이미 존재합니다"
    return ""


def validate_subdomain(sub: str) -> str:
    if not sub:
        return "서브도메인을 입력하세요"
    if not SUB_RE.match(sub):
        return "영문, 숫자, - 만 사용할 수 있습니다"
    return ""


def validate_service(url: str) -> str:
    if not url:
        return "서비스 주소를 입력하세요"
    if not SERVICE_RE.match(url):
        return "http:// 또는 https:// 로 시작하는 주소여야 합니다 (예: http://localhost:8000)"
    return ""


@dataclass
class PlannedStep:
    label: str
    preview: str


def plan_steps(name: str, hostname: str, service: str) -> list[PlannedStep]:
    return [
        PlannedStep("터널 생성", f"cloudflared tunnel create {name}"),
        PlannedStep("DNS 연결", f"cloudflared tunnel route dns {name} {hostname}"),
        PlannedStep("설정 파일 작성",
                    f"~/.cloudflared/config-{name}.yml 생성 ({hostname} → {service})"),
    ]


def execute_creation(client: CloudflaredClient, name: str, hostname: str,
                     service: str,
                     progress: Callable[[int, str, bool], None]) -> None:
    progress(0, "터널 생성 중...", True)
    tid, cred = client.create_tunnel(name)
    progress(0, f"터널 생성 완료 (id: {tid[:8]}...)", True)

    progress(1, "DNS 라우팅 중...", True)
    client.route_dns(name, hostname)
    progress(1, f"{hostname} 연결 완료", True)

    progress(2, "설정 파일 작성 중...", True)
    text = build_config(tid, cred, hostname, service)
    client.runner.write_file(client.config_path(name), text)
    progress(2, "설정 파일 작성 완료", True)
```

- [ ] **Step 4: 통과 확인** — `pytest tests/test_wizard_logic.py -v` → PASS

- [ ] **Step 5: 마법사 UI 구현** (`app/ui/wizard.py`)

QWizard 대신 단계 검증이 쉬운 QDialog + QStackedWidget 사용.

```python
from __future__ import annotations

import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QStackedWidget,
                             QVBoxLayout, QWidget)

from app.context import AppContext
from app.core.store import TunnelMeta
from app.core.wizard_logic import (execute_creation, plan_steps, validate_name,
                                   validate_service, validate_subdomain)


class TunnelWizard(QDialog):
    def __init__(self, ctx: AppContext, existing_names: list[str], parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.existing = existing_names
        self.created_meta: TunnelMeta | None = None
        self._events: list[tuple] = []  # 워커 스레드 → UI 폴링 큐
        self.setWindowTitle("터널 생성")
        self.setMinimumSize(520, 380)

        self.stack = QStackedWidget()
        self.err = QLabel()
        self.err.setStyleSheet("color: #f85149;")
        self.preview = QLabel()
        self.preview.setStyleSheet(
            "font-family: Consolas; color: #ffd28e; background: #1f2330;"
            "padding: 8px; border-radius: 6px;")
        self.preview.setWordWrap(True)

        self.back_btn = QPushButton("← 이전")
        self.next_btn = QPushButton("다음 →")
        self.next_btn.setObjectName("primary")
        self.back_btn.clicked.connect(self._back)
        self.next_btn.clicked.connect(self._next)

        lay = QVBoxLayout(self)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.preview)
        lay.addWidget(self.err)
        nav = QHBoxLayout()
        nav.addWidget(self.back_btn)
        nav.addStretch(1)
        nav.addWidget(self.next_btn)
        lay.addLayout(nav)

        self._build_pages()
        self._go(0)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain_events)
        self._timer.start(150)

    # ---- 페이지 구성 ----
    def _page(self, title: str, *widgets) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        t = QLabel(title)
        t.setStyleSheet("font-size: 16px; font-weight: 700;")
        v.addWidget(t)
        for x in widgets:
            v.addWidget(x)
        v.addStretch(1)
        return w

    def _build_pages(self):
        s = self.ctx.store.settings
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("예: mysite")
        self.sub_edit = QLineEdit()
        self.sub_edit.setPlaceholderText("예: mysite")
        self.domain_edit = QLineEdit(s.root_domain)
        self.domain_edit.setPlaceholderText("예: example.com")
        self.service_edit = QLineEdit("http://localhost:8000")
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setPlaceholderText("예: uvicorn main:app --port 8000 (선택)")
        self.cwd_edit = QLineEdit()
        self.cwd_edit.setPlaceholderText("작업 폴더 (선택)")
        browse = QPushButton("폴더 선택...")
        browse.clicked.connect(lambda: self.cwd_edit.setText(
            QFileDialog.getExistingDirectory(self, "작업 폴더") or self.cwd_edit.text()))
        self.together_chk = QCheckBox("터널을 켤 때 서버도 함께 시작")

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        self.stack.addWidget(self._page("① 터널 이름",
                                        QLabel("터널을 구분할 이름입니다."), self.name_edit))
        self.stack.addWidget(self._page("② 연결할 도메인",
                                        QLabel("서브도메인"), self.sub_edit,
                                        QLabel("루트 도메인"), self.domain_edit))
        self.stack.addWidget(self._page("③ 로컬 서비스 주소",
                                        QLabel("터널이 전달할 로컬 서비스입니다."),
                                        self.service_edit))
        self.stack.addWidget(self._page("④ 웹서버 명령 (선택)",
                                        self.cmd_edit, self.cwd_edit, browse,
                                        self.together_chk))
        self.stack.addWidget(self._page("⑤ 실행", self.status_label))

        for e in (self.name_edit, self.sub_edit, self.domain_edit,
                  self.service_edit):
            e.textChanged.connect(self._update_preview)

    # ---- 네비게이션/검증 ----
    def _hostname(self) -> str:
        return f"{self.sub_edit.text().strip()}.{self.domain_edit.text().strip()}"

    def _validate_current(self) -> str:
        i = self.stack.currentIndex()
        if i == 0:
            return validate_name(self.name_edit.text().strip(), self.existing)
        if i == 1:
            e = validate_subdomain(self.sub_edit.text().strip())
            if not e and not self.domain_edit.text().strip():
                e = "루트 도메인을 입력하세요"
            return e
        if i == 2:
            return validate_service(self.service_edit.text().strip())
        return ""

    def _update_preview(self):
        i = self.stack.currentIndex()
        steps = plan_steps(self.name_edit.text().strip() or "<이름>",
                           self._hostname(), self.service_edit.text().strip())
        if i in (0, 1, 2):
            self.preview.setText("실행될 명령: " + steps[i].preview)
            self.preview.show()
        else:
            self.preview.hide()

    def _go(self, i: int):
        self.stack.setCurrentIndex(i)
        self.back_btn.setVisible(0 < i < 4)
        self.next_btn.setText("생성 시작" if i == 3 else "다음 →")
        self.err.clear()
        self._update_preview()

    def _back(self):
        self._go(self.stack.currentIndex() - 1)

    def _next(self):
        e = self._validate_current()
        if e:
            self.err.setText(e)
            return
        i = self.stack.currentIndex()
        if i < 3:
            self._go(i + 1)
        elif i == 3:
            self._go(4)
            self._start_creation()

    # ---- 실행 ----
    def _start_creation(self):
        self.next_btn.setEnabled(False)
        self.back_btn.hide()
        name = self.name_edit.text().strip()
        hostname = self._hostname()
        service = self.service_edit.text().strip()
        client = self.ctx.client
        lines: list[str] = []

        def progress(idx, msg, ok):
            self._events.append(("log", f"{'✅' if ok else '❌'} {msg}"))

        def work():
            try:
                execute_creation(client, name, hostname, service, progress)
                self._events.append(("done", name, hostname, service))
            except Exception as ex:  # CloudflaredError 포함
                self._events.append(("fail", str(ex)))

        self._log_lines = lines
        threading.Thread(target=work, daemon=True).start()

    def _drain_events(self):
        while self._events:
            ev = self._events.pop(0)
            if ev[0] == "log":
                self.status_label.setText(
                    (self.status_label.text() + "\n" + ev[1]).strip())
            elif ev[0] == "done":
                _, name, hostname, service = ev
                self.created_meta = TunnelMeta(
                    name=name, hostname=hostname, service=service,
                    server_cmd=self.cmd_edit.text().strip(),
                    server_cwd=self.cwd_edit.text().strip(),
                    start_together=self.together_chk.isChecked())
                # 루트 도메인 기억
                self.ctx.store.settings.root_domain = self.domain_edit.text().strip()
                self.next_btn.setText("완료")
                self.next_btn.setEnabled(True)
                self.next_btn.clicked.disconnect()
                self.next_btn.clicked.connect(self.accept)
            elif ev[0] == "fail":
                self.status_label.setText(
                    self.status_label.text() + f"\n❌ 실패: {ev[1]}")
                self.next_btn.setText("닫기")
                self.next_btn.setEnabled(True)
                self.next_btn.clicked.disconnect()
                self.next_btn.clicked.connect(self.reject)
```

- [ ] **Step 6: 스모크 확인** — 전체 `pytest -q` PASS. UI는 Task 10에서 메인 창과 연결 후 실제 생성으로 검증.

- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat: 터널 생성 마법사 (로직 + UI)"`

---

### Task 10: 메인 화면 — 터널 카드 리스트 (main_window.py)

**Files:**
- Create: `app/ui/main_window.py`
- Modify: `app/main.py` (placeholder QMainWindow → MainWindow(ctx))

**Interfaces:**
- Consumes: `AppContext` (Task 7), `TunnelInfo` (Task 4), `TunnelState`/`STATE_COLORS` (Task 5/7), `TunnelWizard` (Task 9), `get_main_ingress`/`parse_config` (Task 3)
- Produces: `MainWindow(ctx)` — 이후 태스크가 다음 훅을 사용:
  - `.refresh()` — 터널 목록 다시 로드
  - `.cards: list[TunnelCard]`, `TunnelCard.tunnel_name: str`
  - `TunnelCard.toggle_tunnel()`, `TunnelCard.toggle_server()` (Task 13 단축키가 호출)
  - `._open_log(name)` (Task 11에서 구현 연결, 이 태스크에서는 stub 메서드)

- [ ] **Step 1: 구현** (`app/ui/main_window.py`)

```python
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QMenu,
                             QMessageBox, QPushButton, QScrollArea, QVBoxLayout,
                             QWidget)

from app.context import AppContext
from app.core.cloudflared import CloudflaredError, TunnelInfo
from app.core.config_yml import get_main_ingress, parse_config
from app.core.process_mgr import TunnelState
from app.core.store import TunnelMeta
from app.ui.theme import STATE_COLORS
from app.ui.wizard import TunnelWizard

STATE_LABELS = {
    TunnelState.STOPPED: "중지됨",
    TunnelState.STARTING: "시작 중...",
    TunnelState.RUNNING: "실행 중",
    TunnelState.ERROR: "오류",
}


class TunnelCard(QFrame):
    def __init__(self, win: "MainWindow", info: TunnelInfo, meta: TunnelMeta):
        super().__init__()
        self.setObjectName("card")
        self.win = win
        self.info = info
        self.meta = meta
        self.tunnel_name = info.name

        self.dot = QLabel("●")
        title = QLabel(info.name)
        title.setObjectName("cardTitle")
        sub = QLabel(f"{meta.hostname or '(도메인 미설정)'}  ·  "
                     f"{meta.service or '(서비스 미설정)'}")
        sub.setObjectName("cardSub")
        self.state_label = QLabel()
        self.state_label.setObjectName("cardSub")

        self.toggle_btn = QPushButton()
        self.server_btn = QPushButton("▶ 서버")
        self.server_btn.setVisible(bool(meta.server_cmd))
        log_btn = QPushButton("📜 로그")
        menu_btn = QPushButton("⋮")
        menu_btn.setFixedWidth(34)

        self.toggle_btn.clicked.connect(self.toggle_tunnel)
        self.server_btn.clicked.connect(self.toggle_server)
        log_btn.clicked.connect(lambda: win._open_log(self.tunnel_name))
        menu_btn.clicked.connect(lambda: self._menu(menu_btn))

        left = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(self.dot)
        top.addWidget(title)
        top.addWidget(self.state_label)
        top.addStretch(1)
        left.addLayout(top)
        left.addWidget(sub)

        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addWidget(self.toggle_btn)
        lay.addWidget(self.server_btn)
        lay.addWidget(log_btn)
        lay.addWidget(menu_btn)
        self.update_state()

    # ---- 동작 ----
    def toggle_tunnel(self):
        ctx = self.win.ctx
        st = ctx.manager.tunnel_state(self.tunnel_name)
        try:
            if st in (TunnelState.STOPPED, TunnelState.ERROR):
                if not ctx.runner.file_exists(ctx.client.config_path(self.tunnel_name)):
                    QMessageBox.warning(self, "설정 없음",
                                        f"config-{self.tunnel_name}.yml 이 없습니다.\n"
                                        "⋮ 메뉴에서 설정을 편집하세요.")
                    return
                ctx.manager.start_tunnel(self.tunnel_name, ctx.runner, ctx.client)
                if self.meta.start_together and self.meta.server_cmd \
                        and not ctx.manager.server_running(self.tunnel_name):
                    ctx.manager.start_server(self.meta, ctx.runner)
            else:
                ctx.manager.stop_tunnel(self.tunnel_name)
        except Exception as ex:
            QMessageBox.critical(self, "오류", str(ex))
        self.update_state()

    def toggle_server(self):
        ctx = self.win.ctx
        try:
            if ctx.manager.server_running(self.tunnel_name):
                ctx.manager.stop_server(self.tunnel_name)
            else:
                ctx.manager.start_server(self.meta, ctx.runner)
        except Exception as ex:
            QMessageBox.critical(self, "서버 오류", str(ex))
        self.update_state()

    def _menu(self, anchor: QPushButton):
        m = QMenu(self)
        edit = QAction("편집", m)
        delete = QAction("삭제", m)
        edit.triggered.connect(lambda: self.win._edit_tunnel(self))
        delete.triggered.connect(lambda: self.win._delete_tunnel(self))
        m.addAction(edit)
        m.addAction(delete)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    # ---- 표시 갱신 ----
    def update_state(self):
        st = self.win.ctx.manager.tunnel_state(self.tunnel_name)
        self.dot.setStyleSheet(f"color: {STATE_COLORS[st]}; font-size: 16px;")
        self.state_label.setText(STATE_LABELS[st])
        running = st in (TunnelState.STARTING, TunnelState.RUNNING)
        self.toggle_btn.setText("■ 끄기" if running else "▶ 켜기")
        if self.meta.server_cmd:
            s_run = self.win.ctx.manager.server_running(self.tunnel_name)
            self.server_btn.setText("■ 서버" if s_run else "▶ 서버")


class MainWindow(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.cards: list[TunnelCard] = []
        self.setWindowTitle("Cloudflare Tunnel GUI")
        self.resize(780, 580)

        # 상단 바
        self.target_combo = QComboBox()
        refresh_btn = QPushButton("🔄 새로고침")
        add_btn = QPushButton("＋ 터널 생성")
        add_btn.setObjectName("primary")
        settings_btn = QPushButton("⚙ 설정")
        refresh_btn.clicked.connect(self.refresh)
        add_btn.clicked.connect(self._create_tunnel)
        settings_btn.clicked.connect(self._open_settings)
        self.target_combo.currentIndexChanged.connect(self._switch_target)

        top = QHBoxLayout()
        top.addWidget(QLabel("대상:"))
        top.addWidget(self.target_combo, 1)
        top.addWidget(refresh_btn)
        top.addWidget(add_btn)
        top.addWidget(settings_btn)

        # 카드 목록
        self.list_lay = QVBoxLayout()
        self.list_lay.addStretch(1)
        inner = QWidget()
        inner.setLayout(self.list_lay)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)

        self.banner = QLabel()
        self.banner.setStyleSheet(
            "background:#3a1d1d;color:#f85149;padding:6px;border-radius:6px;")
        self.banner.hide()

        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self.banner)
        root.addWidget(scroll, 1)

        self._reload_targets()
        self.refresh()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    # ---- 대상 전환 ----
    def _reload_targets(self):
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        self.target_combo.addItem("🖥️ 이 PC", None)
        for p in self.ctx.store.settings.ssh_profiles:
            self.target_combo.addItem(f"🍓 {p.name} ({p.host})", p)
        self.target_combo.blockSignals(False)

    def _switch_target(self):
        profile = self.target_combo.currentData()
        self.banner.hide()
        try:
            if profile is None:
                self.ctx.set_local()
            else:
                self.setCursor(Qt.CursorShape.WaitCursor)
                self.ctx.set_remote(profile)
        except Exception as ex:
            self.banner.setText(f"SSH 연결 실패: {ex}")
            self.banner.show()
            self.target_combo.setCurrentIndex(0)
            self.ctx.set_local()
        finally:
            self.unsetCursor()
        self.refresh()

    # ---- 목록 ----
    def refresh(self):
        try:
            self.setCursor(Qt.CursorShape.WaitCursor)
            infos = self.ctx.client.list_tunnels()
        except Exception as ex:
            self.banner.setText(f"터널 목록 조회 실패: {ex}")
            self.banner.show()
            infos = []
        finally:
            self.unsetCursor()

        for c in self.cards:
            c.setParent(None)
        self.cards.clear()

        metas = self.ctx.store.settings.tunnels
        for info in infos:
            meta = metas.get(info.name) or self._meta_from_config(info.name)
            card = TunnelCard(self, info, meta)
            self.list_lay.insertWidget(self.list_lay.count() - 1, card)
            self.cards.append(card)

    def _meta_from_config(self, name: str) -> TunnelMeta:
        """settings.json에 없는 터널: config yml에서 hostname/service 복원."""
        meta = TunnelMeta(name=name)
        try:
            path = self.ctx.client.config_path(name)
            if self.ctx.runner.file_exists(path):
                cfg = parse_config(self.ctx.runner.read_file(path))
                meta.hostname, meta.service = get_main_ingress(cfg)
        except Exception:
            pass
        self.ctx.store.settings.tunnels[name] = meta
        self.ctx.store.save()
        return meta

    # ---- 생성/편집/삭제 ----
    def _create_tunnel(self):
        wiz = TunnelWizard(self.ctx, [c.tunnel_name for c in self.cards], self)
        if wiz.exec() == wiz.DialogCode.Accepted and wiz.created_meta:
            self.ctx.store.settings.tunnels[wiz.created_meta.name] = wiz.created_meta
            self.ctx.store.save()
            self.refresh()

    def _edit_tunnel(self, card: TunnelCard):
        from app.ui.edit_dialog import EditTunnelDialog  # Task 12
        dlg = EditTunnelDialog(self.ctx, card.meta, self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.ctx.store.save()
            self.refresh()

    def _delete_tunnel(self, card: TunnelCard):
        name = card.tunnel_name
        ok = QMessageBox.question(
            self, "터널 삭제",
            f"'{name}' 터널을 삭제할까요?\n\n"
            "- 실행 중이면 중지됩니다\n"
            "- config 파일이 삭제됩니다\n"
            "- DNS CNAME 레코드는 Cloudflare 대시보드에서 직접 삭제해야 합니다")
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            self.ctx.manager.stop_tunnel(name)
            self.ctx.manager.stop_server(name)
            self.ctx.client.delete_tunnel(name)
            path = self.ctx.client.config_path(name)
            if self.ctx.runner.file_exists(path):
                self.ctx.runner.remove_file(path)
            self.ctx.store.settings.tunnels.pop(name, None)
            self.ctx.store.save()
        except CloudflaredError as ex:
            QMessageBox.critical(self, "삭제 실패", str(ex))
        self.refresh()

    # ---- 이후 태스크에서 연결 ----
    def _open_log(self, name: str):
        pass  # Task 11

    def _open_settings(self):
        pass  # Task 12

    def _tick(self):
        for c in self.cards:
            c.update_state()
```

- [ ] **Step 2: main.py 교체**

```python
    from app.ui.main_window import MainWindow
    win = MainWindow(ctx)
    win.show()
```

- [ ] **Step 3: 통합 스모크 (실제 cloudflared)**

`python -m app.main` 실행 후:
1. 기존 터널이 카드로 표시되는지 (없으면 ＋로 생성)
2. ＋ → 마법사에서 테스트 터널 생성 (이름 `guitest`, 실제 서브도메인, `http://localhost:8000`) → 카드 등장
3. ▶ 켜기 → 🟡 → 🟢 전환 확인 (로컬 서비스 없어도 터널 자체는 RUNNING이 됨)
4. ■ 끄기 → ⚪ 확인
5. ⋮ → 삭제 → 목록에서 사라짐 확인

- [ ] **Step 4: Commit** — `git add -A && git commit -m "feat: 메인 화면 터널 카드 리스트"`

---### Task 11: 로그 뷰어 (log_viewer.py)

**Files:**
- Create: `app/ui/log_viewer.py`
- Modify: `app/ui/main_window.py` (`_open_log` 연결)

**Interfaces:**
- Consumes: `LogBuffer.get_since` (Task 5), `AppContext` (Task 7)
- Produces: `LogViewer(ctx, tunnel_name)` (QDialog, non-modal) — 터널/서버 로그 2개 탭, 200ms 폴링, ERR 하이라이트, 자동 스크롤 토글, 지우기

- [ ] **Step 1: 구현** (`app/ui/log_viewer.py`)

```python
from __future__ import annotations

import html
import re

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QPlainTextEdit,
                             QPushButton, QTabWidget, QVBoxLayout, QWidget)

from app.context import AppContext
from app.core.process_mgr import LogBuffer

ERROR_RE = re.compile(r"\b(ERR|error|failed|Failed)\b")


class _LogTab(QWidget):
    def __init__(self, buffer: LogBuffer):
        super().__init__()
        self.buffer = buffer
        self.seq = 0
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(2000)
        self.follow = QCheckBox("자동 스크롤")
        self.follow.setChecked(True)
        clear_btn = QPushButton("지우기")
        clear_btn.clicked.connect(self._clear)

        bar = QHBoxLayout()
        bar.addWidget(self.follow)
        bar.addStretch(1)
        bar.addWidget(clear_btn)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)

    def poll(self):
        self.seq, lines = self.buffer.get_since(self.seq)
        for line in lines:
            if ERROR_RE.search(line):
                self.view.appendHtml(
                    f'<span style="color:#f85149">{html.escape(line)}</span>')
            else:
                self.view.appendPlainText(line)
        if lines and self.follow.isChecked():
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _clear(self):
        self.view.clear()
        self.buffer.clear()
        self.seq = 0


class LogViewer(QDialog):
    def __init__(self, ctx: AppContext, tunnel_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"로그 — {tunnel_name}")
        self.resize(720, 480)
        self.setModal(False)

        tabs = QTabWidget()
        self.t_tab = _LogTab(ctx.manager.tunnel_log(tunnel_name))
        self.s_tab = _LogTab(ctx.manager.server_log(tunnel_name))
        tabs.addTab(self.t_tab, "터널 (cloudflared)")
        tabs.addTab(self.s_tab, "웹서버")

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(200)

    def _poll(self):
        self.t_tab.poll()
        self.s_tab.poll()
```

- [ ] **Step 2: main_window 연결** — `_open_log` 교체:

```python
    def _open_log(self, name: str):
        from app.ui.log_viewer import LogViewer
        viewer = LogViewer(self.ctx, name, self)
        viewer.show()
```

주의: `ProcessManager.start_tunnel`이 log를 `tunnel_log(name)`로 가져와 쓰기 때문에, 뷰어가 미리 열려 있어도 같은 LogBuffer 인스턴스를 공유한다 (Task 5의 `_make_log` 캐시가 보장).

- [ ] **Step 3: 스모크** — 터널 켜고 로그 버튼 → cloudflared 로그가 실시간으로 흐르고, 오류 라인이 빨갛게 표시되는지 확인.

- [ ] **Step 4: Commit** — `git add -A && git commit -m "feat: 실시간 로그 뷰어"`

---

### Task 12: 편집 다이얼로그 + 설정 화면 (edit_dialog.py, settings.py)

**Files:**
- Create: `app/ui/edit_dialog.py`, `app/ui/settings.py`
- Modify: `app/ui/main_window.py` (`_open_settings` 연결)

**Interfaces:**
- Consumes: `TunnelMeta`/`SshProfile`/`SettingsStore` (Task 1), `update_service` (Task 3), `SshRunner` (Task 6), `AppContext` (Task 7)
- Produces: `EditTunnelDialog(ctx, meta)` — 서비스 주소/서버 명령/함께 시작 편집, 서비스 변경 시 config yml도 갱신. `SettingsDialog(ctx)` — SSH 프로필 CRUD + 연결 테스트, cloudflared 경로

- [ ] **Step 1: 구현** (`app/ui/edit_dialog.py`)

```python
from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QLineEdit, QMessageBox, QPushButton)

from app.context import AppContext
from app.core.config_yml import update_service
from app.core.store import TunnelMeta
from app.core.wizard_logic import validate_service


class EditTunnelDialog(QDialog):
    def __init__(self, ctx: AppContext, meta: TunnelMeta, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.meta = meta
        self.setWindowTitle(f"편집 — {meta.name}")
        self.setMinimumWidth(440)

        self.service_edit = QLineEdit(meta.service)
        self.cmd_edit = QLineEdit(meta.server_cmd)
        self.cwd_edit = QLineEdit(meta.server_cwd)
        browse = QPushButton("폴더 선택...")
        browse.clicked.connect(lambda: self.cwd_edit.setText(
            QFileDialog.getExistingDirectory(self, "작업 폴더") or self.cwd_edit.text()))
        self.together_chk = QCheckBox()
        self.together_chk.setChecked(meta.start_together)

        form = QFormLayout(self)
        form.addRow("서비스 주소", self.service_edit)
        form.addRow("서버 명령", self.cmd_edit)
        form.addRow("작업 폴더", self.cwd_edit)
        form.addRow("", browse)
        form.addRow("터널과 함께 시작", self.together_chk)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _save(self):
        service = self.service_edit.text().strip()
        err = validate_service(service)
        if err:
            QMessageBox.warning(self, "입력 오류", err)
            return
        if service != self.meta.service:
            path = self.ctx.client.config_path(self.meta.name)
            if self.ctx.runner.file_exists(path):
                text = self.ctx.runner.read_file(path)
                self.ctx.runner.write_file(path, update_service(text, service))
        self.meta.service = service
        self.meta.server_cmd = self.cmd_edit.text().strip()
        self.meta.server_cwd = self.cwd_edit.text().strip()
        self.meta.start_together = self.together_chk.isChecked()
        self.accept()
```

- [ ] **Step 2: 구현** (`app/ui/settings.py`)

```python
from __future__ import annotations

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                             QHBoxLayout, QLineEdit, QListWidget, QMessageBox,
                             QPushButton, QSpinBox, QVBoxLayout)

from app.context import AppContext
from app.core.ssh_runner import SshRunner
from app.core.store import SshProfile


class SettingsDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("설정")
        self.setMinimumSize(560, 420)

        # SSH 프로필 목록
        self.listw = QListWidget()
        self.listw.currentRowChanged.connect(self._load_profile)
        add_btn = QPushButton("＋ 추가")
        del_btn = QPushButton("－ 삭제")
        add_btn.clicked.connect(self._add)
        del_btn.clicked.connect(self._delete)

        # 프로필 폼
        self.name_edit = QLineEdit()
        self.host_edit = QLineEdit()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(22)
        self.user_edit = QLineEdit("pi")
        self.key_edit = QLineEdit()
        key_btn = QPushButton("키 파일...")
        key_btn.clicked.connect(lambda: self.key_edit.setText(
            QFileDialog.getOpenFileName(self, "SSH 키 파일")[0] or self.key_edit.text()))
        test_btn = QPushButton("연결 테스트")
        test_btn.clicked.connect(self._test)
        save_btn = QPushButton("프로필 저장")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save_profile)

        form = QFormLayout()
        form.addRow("이름", self.name_edit)
        form.addRow("호스트", self.host_edit)
        form.addRow("포트", self.port_spin)
        form.addRow("사용자", self.user_edit)
        form.addRow("키 파일", self.key_edit)
        form.addRow("", key_btn)
        row = QHBoxLayout()
        row.addWidget(test_btn)
        row.addWidget(save_btn)
        form.addRow(row)

        # cloudflared 경로 재정의
        self.cf_path_edit = QLineEdit(ctx.store.settings.cloudflared_path)
        self.cf_path_edit.setPlaceholderText("비우면 PATH에서 cloudflared 탐색")
        form.addRow("cloudflared 경로", self.cf_path_edit)

        left = QVBoxLayout()
        left.addWidget(self.listw, 1)
        btns = QHBoxLayout()
        btns.addWidget(add_btn)
        btns.addWidget(del_btn)
        left.addLayout(btns)

        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addLayout(form, 2)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self._close)

        root = QVBoxLayout(self)
        root.addLayout(body, 1)
        root.addWidget(close)
        self._reload_list()

    # ---- 목록 ----
    def _profiles(self) -> list[SshProfile]:
        return self.ctx.store.settings.ssh_profiles

    def _reload_list(self):
        self.listw.clear()
        for p in self._profiles():
            self.listw.addItem(f"{p.name} ({p.username}@{p.host}:{p.port})")

    def _load_profile(self, row: int):
        if 0 <= row < len(self._profiles()):
            p = self._profiles()[row]
            self.name_edit.setText(p.name)
            self.host_edit.setText(p.host)
            self.port_spin.setValue(p.port)
            self.user_edit.setText(p.username)
            self.key_edit.setText(p.key_path)

    def _current_form(self) -> SshProfile:
        return SshProfile(name=self.name_edit.text().strip(),
                          host=self.host_edit.text().strip(),
                          port=self.port_spin.value(),
                          username=self.user_edit.text().strip(),
                          key_path=self.key_edit.text().strip())

    def _add(self):
        self.listw.clearSelection()
        self.name_edit.clear()
        self.host_edit.clear()
        self.user_edit.setText("pi")
        self.key_edit.clear()

    def _save_profile(self):
        p = self._current_form()
        if not p.name or not p.host:
            QMessageBox.warning(self, "입력 오류", "이름과 호스트는 필수입니다")
            return
        profiles = self._profiles()
        for i, ex in enumerate(profiles):
            if ex.name == p.name:
                profiles[i] = p
                break
        else:
            profiles.append(p)
        self.ctx.store.save()
        self._reload_list()

    def _delete(self):
        row = self.listw.currentRow()
        if 0 <= row < len(self._profiles()):
            del self._profiles()[row]
            self.ctx.store.save()
            self._reload_list()

    def _test(self):
        try:
            r = SshRunner(self._current_form())
            r.connect()
            res = r.run(["echo", "ok"])
            r.close()
            if res.exit_code == 0:
                QMessageBox.information(self, "연결 테스트", "✅ 연결 성공")
            else:
                QMessageBox.warning(self, "연결 테스트", f"명령 실패: {res.stderr}")
        except Exception as ex:
            QMessageBox.critical(self, "연결 테스트", f"❌ 실패: {ex}")

    def _close(self):
        self.ctx.store.settings.cloudflared_path = self.cf_path_edit.text().strip()
        self.ctx.store.save()
        self.reject()
```

- [ ] **Step 3: main_window 연결** — `_open_settings` 교체:

```python
    def _open_settings(self):
        from app.ui.settings import SettingsDialog
        SettingsDialog(self.ctx, self).exec()
        self._reload_targets()
```

- [ ] **Step 4: 스모크** — 설정에서 SSH 프로필 추가/삭제, settings.json에 반영 확인. 편집 다이얼로그에서 서비스 주소 변경 → config yml 반영 확인.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: 터널 편집 + 설정(SSH 프로필) 화면"`

---

### Task 13: 단축키 + 종료 정리 + 원격 모드 마무리

**Files:**
- Modify: `app/ui/main_window.py`

**Interfaces:**
- Consumes: `TunnelCard.toggle_tunnel/toggle_server` (Task 10), `ProcessManager.any_running/stop_all` (Task 5)
- Produces: `Ctrl+1~9` 터널 토글, `Ctrl+Shift+1~9` 서버 토글, closeEvent 정리

- [ ] **Step 1: MainWindow.__init__ 끝에 단축키 등록 추가**

```python
        from PyQt6.QtGui import QKeySequence, QShortcut
        for i in range(1, 10):
            sc = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            sc.activated.connect(lambda n=i - 1: self._shortcut_tunnel(n))
            sc2 = QShortcut(QKeySequence(f"Ctrl+Shift+{i}"), self)
            sc2.activated.connect(lambda n=i - 1: self._shortcut_server(n))
```

- [ ] **Step 2: 메서드 추가**

```python
    def _shortcut_tunnel(self, idx: int):
        if idx < len(self.cards):
            self.cards[idx].toggle_tunnel()

    def _shortcut_server(self, idx: int):
        if idx < len(self.cards):
            self.cards[idx].toggle_server()

    def closeEvent(self, event):
        if self.ctx.manager.any_running():
            from PyQt6.QtWidgets import QMessageBox
            ok = QMessageBox.question(
                self, "종료", "실행 중인 터널/서버가 있습니다.\n"
                              "종료하면 모두 중지됩니다. 종료할까요?")
            if ok != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.ctx.manager.stop_all()
        self.ctx.set_local()  # SSH 연결 정리
        event.accept()
```

- [ ] **Step 3: 스모크** — 터널 켠 상태에서 Ctrl+1로 토글되는지, 창 닫기 시 확인 다이얼로그 후 프로세스가 모두 종료되는지 (작업 관리자에서 cloudflared 사라짐) 확인.

- [ ] **Step 4: Commit** — `git add -A && git commit -m "feat: 단축키 + 종료 정리"`

---

### Task 14: 수동 통합 체크리스트 + README

**Files:**
- Create: `docs/manual-test-checklist.md`, `README.md`

- [ ] **Step 1: 체크리스트 작성** (`docs/manual-test-checklist.md`)

```markdown
# 수동 통합 테스트 체크리스트

릴리스 전 실제 cloudflared로 전 과정을 확인한다.

## 로컬
- [ ] 앱 시작 → 온보딩 없이 메인 진입 (설치+로그인 완료 상태)
- [ ] 기존 터널이 카드로 표시된다
- [ ] ＋ → 마법사: 이름/도메인/서비스 입력, 각 단계 명령 미리보기 표시
- [ ] 생성 완료 → 카드 등장, config-<이름>.yml 생성 확인
- [ ] ▶ 켜기 → 🟡 → 🟢 (로그에 "Registered tunnel connection")
- [ ] 브라우저에서 <서브도메인>.<도메인> 접속 → 로컬 서비스 응답
- [ ] 웹서버 ▶ → uvicorn 시작, 로그 뷰어 웹서버 탭에 출력
- [ ] "함께 시작" 체크 시 터널 켜면 서버도 시작
- [ ] Ctrl+1 토글 동작
- [ ] ■ 끄기 → ⚪, 프로세스 종료 확인
- [ ] 잘못된 이름/서브도메인 입력 시 마법사가 막는다
- [ ] 이미 존재하는 터널 이름 → 오류 메시지
- [ ] ⋮ → 편집: 서비스 주소 변경 → yml 반영
- [ ] ⋮ → 삭제: 확인 후 터널/설정 제거
- [ ] 창 닫기: 실행 중이면 확인 다이얼로그 → 전체 정리

## 원격 (Rpi)
- [ ] 설정 → SSH 프로필 추가 → 연결 테스트 성공
- [ ] 대상 전환 → Rpi 터널 목록 표시
- [ ] 원격 터널 생성/켜기/끄기/삭제
- [ ] SSH 끊김 시 배너 표시, 로컬로 복귀
- [ ] 대상을 로컬로 되돌리면 로컬 목록 복원
```

- [ ] **Step 2: README 작성** (`README.md`)

```markdown
# Cloudflare Tunnel GUI

cloudflared 터널을 터미널 명령 대신 GUI로 관리하는 데스크톱 앱.

## 기능
- 터널 목록 / 생성 마법사 (create + route dns + config.yml 자동)
- 터널 on/off, 웹서버(uvicorn 등) 동시 실행, 단축키 (Ctrl+1~9)
- 실시간 로그 뷰어
- SSH 원격 모드: 라즈베리파이의 cloudflared를 같은 화면에서 제어

## 실행
```bash
pip install -r requirements.txt
python -m app.main
```

## 테스트
```bash
pytest
```

문서: `docs/superpowers/specs/`(스펙), `docs/future-extensions.md`(확장 방안)
```

- [ ] **Step 3: 체크리스트 실제 수행** — 로컬 항목 전체 수행. 원격 항목은 Rpi 접근 가능 시 수행, 불가하면 "미검증" 표기.

- [ ] **Step 4: Commit** — `git add -A && git commit -m "docs: README + 수동 테스트 체크리스트"`

---

## Self-Review 결과

- 스펙 커버리지: 온보딩(T8), 메인/카드(T10), 마법사(T9), 편집/삭제(T10·12), 로그(T11), 설정/SSH(T12), 단축키/종료(T13), 원격 모드(T6·7·10), 테스트(각 태스크+T14) — 전부 매핑됨
- 스펙 대비 단순화 2건은 Global Constraints에 명시 (루트 도메인 수동 입력, 원격 pgrep 폴링 생략)
- 타입/시그니처 일관성: `CommandRunner` 파일 API에 `remove_file` 포함 (T10 삭제 플로우에서 사용), `_make_log` 캐시로 로그 뷰어-매니저 간 LogBuffer 공유 보장
