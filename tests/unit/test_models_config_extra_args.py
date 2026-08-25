"""Tests for ``ModelConfig.extra_args`` under ADR-026 / issue #477.

Per the ratification ("disable pydantic for extra_args"), ``ModelConfig``
carries ``extra_args`` verbatim with **no pydantic content validation** —
no shell-quoting check, no managed-flag collision guard, no deny-list. The
llauncher-owned deny-list moved to :mod:`llauncher.core.process` and is
enforced exactly once, at launch time, by ``build_command`` — see
``tests/unit/test_process.py::TestBuildCommandDenyList`` for that coverage.
This module pins the *absence* of validation on ``ModelConfig`` itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llauncher.models.config import ModelConfig


@pytest.fixture
def model_file(tmp_path: Path) -> str:
    """A tmp file that satisfies the ``model_path`` existence validator."""
    f = tmp_path / "m.gguf"
    f.write_bytes(b"x")
    return str(f)


class TestExtraArgsNoContentValidation:
    """ADR-026 / issue #477: ``extra_args`` accepts any string, including

    strings that would have been rejected by the pre-#477 deny-list or
    managed-flag collision guard. Construction, assignment, ``from_dict``,
    and ``from_dict_unvalidated`` all agree — there is exactly one shape
    now, not a write-reject / load-warn asymmetry.
    """

    @pytest.mark.parametrize(
        "extra_args",
        [
            "--api-key foo",
            "--api-key=foo",
            "--alias evil",
            "--alias=evil",
            "--host 0.0.0.0",
            "--port 9999",
            "-m /other/model.gguf",
            "--model /other/model.gguf",
            "--ubatch-size 4096",
            "--parallel=4",
            "-ctk q8_0",
            "-ctv=q8_0",
        ],
    )
    def test_construction_accepts_formerly_denied_flags(
        self, model_file: str, extra_args: str
    ) -> None:
        cfg = ModelConfig(name="m", model_path=model_file, extra_args=extra_args)
        assert cfg.extra_args == extra_args

    def test_construction_accepts_benign_flags(self, model_file: str) -> None:
        cfg = ModelConfig(
            name="m",
            model_path=model_file,
            extra_args="--ctx-size 4096 --log-disable --verbose",
        )
        assert cfg.extra_args == "--ctx-size 4096 --log-disable --verbose"

    def test_construction_accepts_empty(self, model_file: str) -> None:
        cfg = ModelConfig(name="m", model_path=model_file, extra_args="")
        assert cfg.extra_args == ""

    def test_construction_accepts_malformed_shell_quoting(self, model_file: str) -> None:
        """Pre-#477 this raised at construction; now it's accepted here and
        would only surface as a ``shlex.split`` error at ``build_command``
        launch time (see ``test_process.py``)."""
        cfg = ModelConfig(name="m", model_path=model_file, extra_args='--foo "unbalanced')
        assert cfg.extra_args == '--foo "unbalanced'

    def test_assignment_accepts_formerly_denied_flags(self, model_file: str) -> None:
        cfg = ModelConfig(name="m", model_path=model_file, extra_args="")
        cfg.extra_args = "--api-key leaked"
        assert cfg.extra_args == "--api-key leaked"
        cfg.extra_args = "--alias=impostor"
        assert cfg.extra_args == "--alias=impostor"

    def test_from_dict_accepts_formerly_denied_flags(self, model_file: str) -> None:
        cfg = ModelConfig.from_dict({
            "name": "m",
            "model_path": model_file,
            "extra_args": "--api-key leak",
        })
        assert cfg.extra_args == "--api-key leak"

    def test_from_dict_unvalidated_accepts_formerly_denied_flags(self) -> None:
        cfg = ModelConfig.from_dict_unvalidated({
            "name": "m",
            "model_path": "/fake/does-not-matter.gguf",
            "extra_args": "--alias sneaky",
        })
        assert cfg.extra_args == "--alias sneaky"

    def test_no_warning_emitted_for_a_formerly_managed_flag(self) -> None:
        """Pre-#477 loading a managed-flag collision emitted a UserWarning.

        That warn-but-tolerate asymmetry is deleted outright — this must
        not warn under ``-W error::UserWarning`` (see also the
        whole-registry load coverage in
        ``tests/unit/test_config_migration_026.py``).
        """
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            cfg = ModelConfig.from_dict_unvalidated({
                "name": "m",
                "model_path": "/fake/does-not-matter.gguf",
                "extra_args": "-ctk q8_0 -ctv q8_0 --ubatch-size 2048",
            })
        assert cfg.extra_args == "-ctk q8_0 -ctv q8_0 --ubatch-size 2048"

    def test_legacy_list_extra_args_normalized_to_string(self) -> None:
        """The legacy ``list[str]`` shape is still joined to a string in

        ``from_dict_unvalidated`` — that migration is unrelated to content
        validation and survives ADR-026 unchanged.
        """
        cfg = ModelConfig.from_dict_unvalidated({
            "name": "m",
            "model_path": "/fake/does-not-matter.gguf",
            "extra_args": ["--api-key", "leak"],
        })
        assert cfg.extra_args == "--api-key leak"

    def test_substring_flag_untouched(self, model_file: str) -> None:
        cfg = ModelConfig(
            name="m",
            model_path=model_file,
            extra_args="--api-key-file /tmp/keys",
        )
        assert cfg.extra_args == "--api-key-file /tmp/keys"


class TestExtraArgsFieldNoLongerExported:
    """The pre-#477 deny-list / managed-flag machinery is gone from

    ``llauncher.models.config`` entirely — it moved to
    ``llauncher.core.process`` (deny-list) or was deleted outright
    (managed-flag collision table).
    """

    def test_denied_extra_arg_flags_not_in_models_config(self) -> None:
        import llauncher.models.config as models_config

        assert not hasattr(models_config, "DENIED_EXTRA_ARG_FLAGS")

    def test_managed_native_flag_machinery_removed(self) -> None:
        import llauncher.models.config as models_config

        assert not hasattr(models_config, "MANAGED_NATIVE_FLAG_TO_FIELD")
        assert not hasattr(models_config, "MANAGED_NATIVE_FLAGS")
        assert not hasattr(models_config.ModelConfig, "extra_args_no_managed_flags")

    def test_denied_extra_arg_flags_lives_in_process(self) -> None:
        from llauncher.core.process import DENIED_EXTRA_ARG_FLAGS

        assert "--api-key" in DENIED_EXTRA_ARG_FLAGS
        assert "--alias" in DENIED_EXTRA_ARG_FLAGS
        assert isinstance(DENIED_EXTRA_ARG_FLAGS, frozenset)

    def test_runtime_binding_flags_present(self) -> None:
        """``--host`` / ``--port`` / ``-m`` / ``--model`` are set by
        ``build_command`` from runtime parameters and managed fields;
        duplication via ``extra_args`` bypasses ADR-LLNCH-010 / model_path
        validation.
        """
        from llauncher.core.process import DENIED_EXTRA_ARG_FLAGS

        for flag in ("--host", "--port", "-m", "--model"):
            assert flag in DENIED_EXTRA_ARG_FLAGS, flag
