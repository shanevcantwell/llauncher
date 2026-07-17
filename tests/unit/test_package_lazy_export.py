"""Tests for ``llauncher``'s lazy ``LauncherState`` re-export (issue #215).

``llauncher/__init__.py`` used to import ``llauncher.state`` eagerly —
purely as a side effect of ``import llauncher`` — which transitively
resolved ``LAUNCHER_STATE_DIR`` (via ``llauncher.core.settings``) before
the CLI's ``--state-dir`` root callback ever ran, defeating the
override for every subcommand. The re-export is now lazy (PEP 562
``__getattr__``); these tests pin that it still behaves like a normal
module attribute for both the happy path and an unknown name.
"""

import llauncher


def test_launcher_state_attribute_resolves_lazily():
    """``llauncher.LauncherState`` still resolves to the real class."""
    from llauncher.state import LauncherState as DirectLauncherState

    assert llauncher.LauncherState is DirectLauncherState


def test_from_import_still_works():
    """``from llauncher import LauncherState`` uses the same lazy path."""
    from llauncher import LauncherState
    from llauncher.state import LauncherState as DirectLauncherState

    assert LauncherState is DirectLauncherState


def test_unknown_attribute_raises_attribute_error():
    """An unrecognized name still raises AttributeError, not a lookup crash."""
    import pytest

    with pytest.raises(AttributeError, match="no attribute 'DoesNotExist'"):
        llauncher.DoesNotExist
