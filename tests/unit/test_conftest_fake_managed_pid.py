"""Self-tests for the ``fake_managed_pid`` conftest fixture (#466 Phase 1).

The fixture has no production consumers yet — it lands in Phase 1 so Phase 2
has it before it needs it (PR #470 review, F5). Without these tests, a
regression in the fixture would surface only as a confusing failure inside
whichever Phase 2 test first adopted it. These exercise the real fixture
against the real ``llauncher.core.process.verify_pid`` seam it patches.
"""

import logging
from unittest.mock import MagicMock

import psutil

from llauncher.core import process as proc_mod


class TestFakeManagedPidStub:
    """A registered pid resolves to the stubbed ``ServerProcessInfo``."""

    def test_registered_pid_returns_stub_with_registered_fields(
        self, fake_managed_pid
    ):
        lock = fake_managed_pid(
            8081, "test-model", 4242,
            alias="my-alias", model_path="/models/a.gguf", create_time=1234.5,
        )

        assert lock.pid == 4242
        assert lock.port == 8081

        info = proc_mod.verify_pid(4242, expect_port=8081)
        assert info == proc_mod.ServerProcessInfo(
            pid=4242,
            port=8081,
            alias="my-alias",
            model_path="/models/a.gguf",
            create_time=1234.5,
            cmdline_unreadable=False,
        )

    def test_alias_defaults_to_model_name(self, fake_managed_pid):
        """ONE-MINT: the launched alias is the ``ModelConfig.name`` (#423)."""
        fake_managed_pid(8082, "embeddinggemma-300M", 777)

        info = proc_mod.verify_pid(777)
        assert info.alias == "embeddinggemma-300M"

    def test_cmdline_unreadable_is_threaded_into_the_stub(self, fake_managed_pid):
        """F5(a): Phase 2's #208 case — present but argv unreadable."""
        fake_managed_pid(8083, "cross-uid-model", 999, cmdline_unreadable=True)

        info = proc_mod.verify_pid(999)
        assert info is not None
        assert info.cmdline_unreadable is True
        assert info.pid == 999

    def test_cmdline_unreadable_defaults_false(self, fake_managed_pid):
        fake_managed_pid(8084, "readable-model", 1001)

        assert proc_mod.verify_pid(1001).cmdline_unreadable is False


class TestFakeManagedPidPortGate:
    """F5(b): the stub's ``expect_port`` gate mirrors the real refusal."""

    def test_expect_port_mismatch_returns_none_and_warns(
        self, fake_managed_pid, caplog
    ):
        fake_managed_pid(8081, "test-model", 4242)

        with caplog.at_level(logging.WARNING, logger="llauncher.core.process"):
            info = proc_mod.verify_pid(4242, expect_port=9999)

        assert info is None
        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "verify_pid" in m and "4242" in m and "9999" in m and "ADR-LLNCH-008" in m
            for m in messages
        ), messages

    def test_matching_expect_port_does_not_warn(self, fake_managed_pid, caplog):
        fake_managed_pid(8081, "test-model", 4242)

        with caplog.at_level(logging.WARNING, logger="llauncher.core.process"):
            info = proc_mod.verify_pid(4242, expect_port=8081)

        assert info is not None
        assert caplog.records == []


class TestFakeManagedPidFallthrough:
    """An unregistered pid runs the real ``verify_pid`` body, not the stub."""

    def test_unregistered_dead_pid_hits_real_verify_pid(
        self, fake_managed_pid, monkeypatch
    ):
        """The real body's ``NoSuchProcess`` arm answers, so the answer is None.

        The stub never returns ``None`` for a *missing* registration — it
        delegates — so observing the real body's dead-pid verdict proves the
        fall-through actually executed real code.
        """
        fake_managed_pid(8081, "test-model", 4242)  # a *different* pid

        def _raise(pid):
            raise psutil.NoSuchProcess(pid=pid)

        monkeypatch.setattr(proc_mod.psutil, "Process", _raise)

        assert proc_mod.verify_pid(31337) is None
        # ...while the registered pid still short-circuits to the stub,
        # never reaching the patched psutil.
        assert proc_mod.verify_pid(4242).pid == 4242

    def test_unregistered_live_pid_is_attributed_by_real_verify_pid(
        self, fake_managed_pid, monkeypatch
    ):
        """Fall-through populates fields from argv, which only real code does."""
        fake_managed_pid(8081, "test-model", 4242)

        p = MagicMock()
        p.is_running.return_value = True
        p.status.return_value = psutil.STATUS_RUNNING
        p.name.return_value = "llama-server"
        p.info = {"pid": p.pid, "name": "llama-server"}
        p.cmdline.return_value = [
            "llama-server", "--port", "8090", "--alias", "foreign",
            "-m", "/models/foreign.gguf",
        ]
        p.create_time.return_value = 42.0

        monkeypatch.setattr(proc_mod.psutil, "Process", lambda pid: p)

        info = proc_mod.verify_pid(31337, expect_port=8090)
        assert info == proc_mod.ServerProcessInfo(
            pid=31337,
            port=8090,
            alias="foreign",
            model_path="/models/foreign.gguf",
            create_time=42.0,
            cmdline_unreadable=False,
        )
