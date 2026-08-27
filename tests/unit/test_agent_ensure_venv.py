"""Guards for ADR-LLNCH-023 Phase A — agent venv recomposition (issue #227).

Two surfaces are exercised here:

1. ``scripts/run.sh setup`` — the single, named recompose command the system
   ensure unit calls. It must (a) lazily no-op when the agent entry point is
   already present (OQ3 lazy re-heal), (b) populate the venv via an editable
   install of ``pyproject.toml`` when missing (OQ2 minimal — no lockfile), and
   (c) fail loud (nonzero) on a broken recompose so the ensure unit enters
   ``failed`` and the agent never starts degraded.

2. The static wiring — the ``llauncher-agent-ensure-venv.service.in`` oneshot
   template, the agent system unit's ``Requires=``/``After=`` on it, and
   ``install.sh``'s render+enable+uninstall mechanics.

The ``run.sh`` tests drive the *real* script in a hermetic temp ``PROJECT_DIR``
with a *fake* ``.venv`` (a stub ``activate`` + ``pip``), so no network ``pip
install`` runs and the real repo is never touched — mirroring the fixture style
of ``test_run_sh_install_honesty.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """Walk up from this file until a ``.git`` entry is found."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


SYSTEMD_DIR = _repo_root() / "scripts" / "systemd"
ENSURE_TEMPLATE = SYSTEMD_DIR / "llauncher-agent-ensure-venv.service.in"
AGENT_SYSTEM_UNIT = SYSTEMD_DIR / "llauncher-agent.service.system.in"
INSTALL_SH = SYSTEMD_DIR / "install.sh"


# ───────────────────────── run.sh setup behavior ───────────────────────


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A hermetic PROJECT_DIR with the real ``run.sh`` and a ``pyproject``.

    ``run.sh`` derives ``PROJECT_DIR`` as the parent of its own ``scripts``
    dir, so everything stays under ``tmp_path``.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(_repo_root() / "scripts" / "run.sh", scripts_dir / "run.sh")
    # A pyproject must exist so the editable-install target is plausible; the
    # stub pip ignores its contents.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='stub'\n")
    return tmp_path


def _make_fake_venv(project_dir: Path, *, pip_creates_entrypoint: bool,
                    pip_returns: int) -> Path:
    """Build a fake ``.venv`` with stub ``activate`` + ``pip``.

    ``activate`` prepends the venv ``bin`` to PATH (so ``pip`` resolves to the
    stub), matching what a real venv activate does. The stub ``pip`` simulates
    an editable install: optionally drops the ``llauncher-agent`` entry point
    and exits with ``pip_returns``.
    """
    venv_bin = project_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)

    (venv_bin / "activate").write_text(f'export PATH="{venv_bin}:$PATH"\n')

    create = (
        f'touch "{venv_bin}/llauncher-agent" && chmod +x "{venv_bin}/llauncher-agent"\n'
        if pip_creates_entrypoint
        else ""
    )
    pip = venv_bin / "pip"
    pip.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "install" ]; then\n'
        f"  {create}"
        f"  exit {pip_returns}\n"
        "fi\n"
        "exit 0\n"
    )
    pip.chmod(0o755)
    return venv_bin


def _run_setup(project_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(project_dir / "scripts" / "run.sh"), "setup"],
        capture_output=True,
        text=True,
        check=False,
        # Drop the test venv's bin from PATH so only the stub pip is reachable.
        env={**os.environ, "VIRTUAL_ENV": ""},
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "invokes `bash scripts/run.sh setup` against a copied run.sh with "
        "a #!/bin/bash shebang; the setup/venv-populate flow is POSIX-only"
    ),
)
def test_setup_lazy_noop_when_entrypoint_present(project_dir: Path):
    """OQ3: present entry point ⇒ no recompose, clean exit."""
    venv_bin = project_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    ep = venv_bin / "llauncher-agent"
    ep.write_text("#!/bin/sh\n")
    ep.chmod(0o755)

    result = _run_setup(project_dir)

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "already populated" in combined
    # It must NOT have entered the recompose branch.
    assert "Recomposing" not in combined


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "invokes `bash scripts/run.sh setup` against a copied run.sh with "
        "a #!/bin/bash shebang; the setup/venv-populate flow is POSIX-only"
    ),
)
def test_setup_populates_when_missing(project_dir: Path):
    """Missing entry point ⇒ editable install runs and yields the entry point."""
    venv_bin = _make_fake_venv(
        project_dir, pip_creates_entrypoint=True, pip_returns=0
    )

    result = _run_setup(project_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "Recomposing agent venv" in combined
    assert "recomposed" in combined
    assert (venv_bin / "llauncher-agent").exists()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "invokes `bash scripts/run.sh setup` against a copied run.sh with "
        "a #!/bin/bash shebang; the setup/venv-populate flow is POSIX-only"
    ),
)
def test_setup_fails_loud_on_pip_error(project_dir: Path):
    """Fail-loud: nonzero pip ⇒ nonzero exit + actionable message, no success."""
    _make_fake_venv(project_dir, pip_creates_entrypoint=False, pip_returns=1)

    result = _run_setup(project_dir)

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "FAILED" in combined
    assert "run.sh setup" in combined  # actionable remediation
    assert "recomposed" not in combined


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "invokes `bash scripts/run.sh setup` against a copied run.sh with "
        "a #!/bin/bash shebang; the setup/venv-populate flow is POSIX-only"
    ),
)
def test_setup_fails_loud_when_entrypoint_absent_after_install(project_dir: Path):
    """A pip that 'succeeds' but leaves no entry point must still fail loud."""
    _make_fake_venv(project_dir, pip_creates_entrypoint=False, pip_returns=0)

    result = _run_setup(project_dir)

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "INCOMPLETE" in combined
    assert "recomposed" not in combined


def test_setup_listed_in_help(project_dir: Path):
    """The new subcommand is discoverable from the help output."""
    result = subprocess.run(
        ["bash", str(project_dir / "scripts" / "run.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert "setup" in combined


# ───────────────────────── static unit / install wiring ────────────────


def test_ensure_unit_template_shape():
    """The oneshot ensure template carries the ADR-LLNCH-023 OQ guarantees."""
    text = ENSURE_TEMPLATE.read_text()
    assert "Type=oneshot" in text
    assert "RemainAfterExit=yes" in text
    assert "TimeoutStartSec=300" in text  # OQ4
    assert "ExecStart=@PROJECT_DIR@/scripts/run.sh setup" in text
    # Runs as root: no active User= directive (comment lines start with ';').
    directives = [
        ln.strip() for ln in text.splitlines() if not ln.lstrip().startswith(";")
    ]
    assert not any(ln.startswith("User=") for ln in directives)


def test_agent_system_unit_requires_ensure_unit():
    """The agent never starts without the guaranteed venv (Requires + After)."""
    text = AGENT_SYSTEM_UNIT.read_text()
    assert "Requires=llauncher-agent-ensure-venv.service" in text
    assert "After=llauncher-agent-ensure-venv.service" in text


def test_user_agent_unit_has_no_cross_scope_dependency():
    """The --user agent unit must NOT Requires= a system-scope ensure unit."""
    text = (SYSTEMD_DIR / "llauncher-agent.service.in").read_text()
    assert "ensure-venv" not in text


def test_install_sh_wires_ensure_unit_in_system_mode():
    """install.sh renders + enables + uninstalls the ensure unit (system only)."""
    text = INSTALL_SH.read_text()
    assert 'ENSURE_UNIT_NAME="llauncher-agent-ensure-venv.service"' in text
    assert "llauncher-agent-ensure-venv.service.in" in text  # template render
    assert 'enable "$ENSURE_UNIT_NAME"' in text
    assert 'disable --now "$ENSURE_UNIT_NAME"' in text  # uninstall path


def test_install_sh_dead_pointer_replaced():
    """The disabled 'run.sh install' pointer is replaced by 'run.sh setup'."""
    text = INSTALL_SH.read_text()
    assert "run.sh install" not in text
    assert "run.sh setup" in text
