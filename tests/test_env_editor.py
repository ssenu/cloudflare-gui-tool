"""환경설정(.env) 편집 다이얼로그: 저장 시 주석 보존, 검증, 삭제 반영."""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.context import AppContext
from app.core.store import RepoMeta, SettingsStore
from app.ui.env_editor import EnvEditorDialog
from tests.fake_runner import FakeRunner


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _silence_message_box(monkeypatch):
    # 저장 성공 안내 팝업이 테스트를 멈추지 않게 한다.
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)


REPO = RepoMeta(id="r1", name="app", url="git@x:a/b.git", path="/srv/apps/app")


def make_dialog(tmp_path, env_text: str | None = None):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    ctx = AppContext(store=store)
    runner = FakeRunner(home="/home/pi")
    runner.name = "ssh:webPi"
    if env_text is not None:
        runner.files["/srv/apps/app/.env"] = env_text
    ctx.runner = runner
    return EnvEditorDialog(ctx, REPO, None), runner


def test_loads_existing_values(qapp, tmp_path):
    dlg, _ = make_dialog(tmp_path, "HOST_PORT=8001\nADMIN_PASSWORD=secret\n")
    assert dlg.port_edit.text() == "8001"
    assert [(r.key(), r.value()) for r in dlg.rows] == [("ADMIN_PASSWORD", "secret")]


def test_secret_value_is_masked_until_toggled(qapp, tmp_path):
    from PyQt6.QtWidgets import QLineEdit

    dlg, _ = make_dialog(tmp_path, "ADMIN_PASSWORD=secret\nHOST_PORT=8000\n")
    row = dlg.rows[0]
    assert row.value_edit.echoMode() == QLineEdit.EchoMode.Password
    dlg.show_chk.setChecked(True)
    assert row.value_edit.echoMode() == QLineEdit.EchoMode.Normal


def test_save_preserves_comments(qapp, tmp_path):
    text = "# 관리자 비밀번호\nADMIN_PASSWORD=old\nHOST_PORT=8000\n"
    dlg, runner = make_dialog(tmp_path, text)
    dlg.port_edit.setText("8001")
    dlg._on_save()

    saved = runner.files["/srv/apps/app/.env"]
    assert "# 관리자 비밀번호" in saved  # 주석이 살아남아야 한다
    assert "HOST_PORT=8001" in saved
    assert "ADMIN_PASSWORD=old" in saved


def test_save_writes_new_file_when_missing(qapp, tmp_path):
    dlg, runner = make_dialog(tmp_path, None)  # .env 없음
    dlg.port_edit.setText("8002")
    dlg._add_row("GATE_PASSWORD", "pw")
    dlg._on_save()

    saved = runner.files["/srv/apps/app/.env"]
    assert "HOST_PORT=8002" in saved
    assert "GATE_PASSWORD=pw" in saved


def test_invalid_port_blocks_save(qapp, tmp_path):
    dlg, runner = make_dialog(tmp_path, "HOST_PORT=8000\n")
    dlg.port_edit.setText("80")
    dlg._on_save()

    assert "1024 이상" in dlg.err.text()
    assert runner.files["/srv/apps/app/.env"] == "HOST_PORT=8000\n"  # 그대로


def test_invalid_key_blocks_save(qapp, tmp_path):
    dlg, runner = make_dialog(tmp_path, "A=1\n")
    dlg._add_row("잘못된 키", "x")
    dlg._on_save()

    assert dlg.err.text() != ""
    assert runner.files["/srv/apps/app/.env"] == "A=1\n"


def test_duplicate_key_blocks_save(qapp, tmp_path):
    dlg, _ = make_dialog(tmp_path, "A=1\n")
    dlg._add_row("A", "2")
    dlg._on_save()
    assert "중복" in dlg.err.text()


def test_host_port_in_table_is_rejected(qapp, tmp_path):
    # 포트는 전용 칸에서만 다룬다 - 두 곳에서 쓰면 어느 쪽이 이길지 모호해진다.
    dlg, _ = make_dialog(tmp_path, "A=1\n")
    dlg._add_row("HOST_PORT", "9000")
    dlg._on_save()
    assert "호스트 포트" in dlg.err.text()


def test_deleted_row_is_removed_from_file(qapp, tmp_path):
    dlg, runner = make_dialog(tmp_path, "# A 설명\nA=1\nB=2\n")
    dlg._delete_row(dlg.rows[0])  # A 삭제
    dlg._on_save()

    saved = runner.files["/srv/apps/app/.env"]
    assert "A=1" not in saved
    assert "B=2" in saved
    assert "# A 설명" in saved  # 주석은 사용자의 것이므로 남긴다


def test_empty_port_removes_key(qapp, tmp_path):
    dlg, runner = make_dialog(tmp_path, "HOST_PORT=8001\nA=1\n")
    dlg.port_edit.setText("")
    dlg._on_save()

    saved = runner.files["/srv/apps/app/.env"]
    assert "HOST_PORT" not in saved  # compose 기본값을 쓰겠다는 뜻
    assert "A=1" in saved


def test_write_failure_shows_error_and_does_not_close(qapp, tmp_path):
    dlg, runner = make_dialog(tmp_path, "A=1\n")

    def boom(_path, _text):
        raise OSError("권한 없음")

    runner.write_file = boom
    dlg._on_save()

    assert "저장 실패" in dlg.err.text()
    assert not dlg.saved
