"""업데이트 화면 흐름: 확인 결과에 따라 무엇을 묻고 무엇을 하는가."""
from __future__ import annotations

import json
import time

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from app.core.store import SettingsStore
from app.core.updater import Release
from app.ui.update_flow import UpdateFlow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path):
    s = SettingsStore(path=str(tmp_path / "settings.json"))
    s.load()
    return s


def release(version="0.2.0", setup=True):
    return Release(version=version, notes="- 새 기능", page_url="https://x/rel",
                   setup_url="https://x/setup.exe" if setup else "", setup_size=30 << 20)


def run_check(flow, manual):
    flow.check(manual=manual)
    end = time.monotonic() + 3
    while flow._poller.is_busy() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()


def test_auto_check_emits_only_when_newer(qapp, store):
    w = QWidget()
    flow = UpdateFlow(w, store, current="0.1.3", kind="installed",
                      fetch=lambda: release("0.2.0"))
    got = []
    flow.available.connect(got.append)

    run_check(flow, manual=False)

    assert [r.version for r in got] == ["0.2.0"]


def test_auto_check_is_silent_when_up_to_date(qapp, store, monkeypatch):
    """자동 확인은 최신일 때 아무것도 띄우지 않는다 - 켤 때마다 창이 뜨면 거슬린다."""
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a)))
    w = QWidget()
    flow = UpdateFlow(w, store, current="0.2.0", kind="installed",
                      fetch=lambda: release("0.2.0"))
    got = []
    flow.available.connect(got.append)

    run_check(flow, manual=False)

    assert got == [] and shown == []


def test_manual_check_says_so_when_up_to_date(qapp, store, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    flow = UpdateFlow(QWidget(), store, current="0.2.0", kind="installed",
                      fetch=lambda: release("0.2.0"))

    run_check(flow, manual=True)

    assert shown and "최신" in shown[0]


def test_manual_check_reports_network_failure(qapp, store, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))

    def boom():
        raise OSError("연결 안 됨")

    flow = UpdateFlow(QWidget(), store, current="0.1.3", kind="installed", fetch=boom)
    run_check(flow, manual=True)

    assert shown and "연결" in shown[0]


def test_check_records_the_time(qapp, store):
    """하루 한 번 규칙이 지켜지려면 확인 시각이 저장돼야 한다."""
    flow = UpdateFlow(QWidget(), store, current="0.2.0", kind="installed",
                      fetch=lambda: release("0.2.0"))
    before = time.time()

    run_check(flow, manual=False)

    saved = json.load(open(store.path, encoding="utf-8"))
    assert saved["last_update_check"] >= before


def test_installed_build_offers_to_install(qapp, store, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    flow = UpdateFlow(QWidget(), store, current="0.1.3", kind="installed")
    installed = []
    flow.install = lambda r: installed.append(r.version)

    flow.offer(release("0.2.0"))

    assert installed == ["0.2.0"]


def test_portable_build_only_opens_the_release_page(qapp, store, monkeypatch):
    """zip으로 푼 앱은 덮어쓸 위치를 앱이 정할 수 없다 - 설치하지 않는다."""
    from PyQt6.QtGui import QDesktopServices
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes))
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda u: opened.append(u.toString())))
    flow = UpdateFlow(QWidget(), store, current="0.1.3", kind="portable")
    flow.install = lambda r: pytest.fail("무설치판에서 설치를 시도했다")

    flow.offer(release("0.2.0"))

    assert opened == ["https://x/rel"]
    assert "무설치" in asked[0]


def test_release_without_setup_asset_falls_back_to_page(qapp, store, monkeypatch):
    """CI가 설치본을 올리기 전에 확인하면 설치본 주소가 없다."""
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    flow = UpdateFlow(QWidget(), store, current="0.1.3", kind="installed")
    flow.install = lambda r: pytest.fail("설치본이 없는데 설치를 시도했다")

    flow.offer(release("0.2.0", setup=False))


# ---- 저장 필드 ----

def test_update_settings_roundtrip(tmp_path):
    p = str(tmp_path / "s.json")
    s = SettingsStore(path=p)
    s.load()
    s.settings.auto_update_check = False
    s.settings.last_update_check = 1234.5
    s.save()

    again = SettingsStore(path=p).load()
    assert again.auto_update_check is False
    assert again.last_update_check == 1234.5


def test_old_settings_file_defaults_to_auto_check_on(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"root_domain": "x"}), encoding="utf-8")

    s = SettingsStore(path=str(p)).load()

    assert s.auto_update_check is True
    assert s.last_update_check == 0.0
