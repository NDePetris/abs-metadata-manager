"""Safety boundaries of the synthetic development harness."""
import os
import re
import shutil
import socket
import sqlite3
from pathlib import Path

import pytest


@pytest.mark.parametrize("host", ["example.com", "192.168.1.10", "0.0.0.0", "::ffff:192.168.1.10"])
def test_external_network_is_blocked_before_dns_or_connect(host):
    with pytest.raises(AssertionError, match="loopback"):
        socket.getaddrinfo(host, 443)
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="loopback"):
            sock.connect((host, 443))
        with pytest.raises(AssertionError, match="loopback"):
            sock.connect_ex((host, 443))


def test_default_app_paths_are_temporary(tmp_path):
    for key in ("ABS_MM_DB", "ABS_MM_SETTINGS", "LOCALAPPDATA"):
        assert Path(os.environ[key]).is_relative_to(tmp_path)
    assert not any(key in os.environ for key in ("ABS_TOKEN", "ABS_SERVER", "GOOGLE_BOOKS_API_KEY"))
    assert Path.home().is_relative_to(tmp_path)
    assert Path(os.environ["NETRC"]).is_relative_to(tmp_path)


def test_documented_backup_and_restore_preserve_wal_decisions_and_uncertain_journal(setup, tmp_path, monkeypatch):
    from abs_manager.store import Store
    from abs_manager.workflow import SafetyError, Workflow, confirmation, preview

    store, api, aid = setup
    # Keep a WAL connection open so copying the base file alone is insufficient.
    with sqlite3.connect(store.path) as keeper:
        assert keeper.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        keeper.execute("SELECT * FROM audits").fetchall()
        store.confirm_targets(aid, "current-id", {"title": "Accepted correction"})
        ids = [f["id"] for f in store.findings(aid) if f["kind"] == "confirmed_target"]
        store.review(ids, "approved")
        plan = preview(store, aid)
        api.fail = "after"
        with pytest.raises(SafetyError, match="uncertain"):
            Workflow(store, api).apply(plan, confirmation(plan))
        assert Path(str(store.path) + "-wal").exists()

        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
        script = re.search(r"@'\n(.*?)\n'@ \|", readme, re.DOTALL).group(1)
        backup = tmp_path / "backups" / "consistent.sqlite3"
        monkeypatch.setattr("sys.argv", ["backup", str(store.path), str(backup)])
        exec(compile(script, "README backup example", "exec"), {})  # noqa: S102 - test the checked-in example

        restored = tmp_path / "restored.sqlite3"
        shutil.copyfile(backup, restored)
        with sqlite3.connect(restored) as db:
            assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            tables = keeper.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            for (name,) in tables:
                # Table names come only from the synthetic app schema.
                assert db.execute(f'SELECT * FROM "{name}"').fetchall() == keeper.execute(
                    f'SELECT * FROM "{name}"').fetchall()
        assert Store(restored).history() == store.history()
        assert Store(restored).unresolved(api.server)
        with pytest.raises(FileExistsError):
            exec(compile(script, "README backup example", "exec"), {})  # noqa: S102 - checked-in example
