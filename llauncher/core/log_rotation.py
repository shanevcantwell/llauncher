"""Size-based rotation for llauncher per-server log files (ADR-LLNCH-013).

Each `llama-server` process writes to ``{LOG_DIR}/{stem}-{port}.log``,
where ``stem`` comes from :func:`llauncher.core.process.log_stem_for`
(sanitized model name plus a short hash — issues #63/#146).
Logs are now opened in append mode (so a restart does not destroy the
previous run's output), which means files grow unboundedly without
intervention. This module provides a simple size-cap rotation:

    foo-8081.log       (live)
    foo-8081.log.1     (most recent rotation)
    foo-8081.log.2
    foo-8081.log.3     (oldest kept; older drops off)

Rotation is **opportunistic** — it runs at process-start time, not on
every write. The active log file is rotated *only if* its current size
already exceeds ``max_bytes``; this avoids rotating an empty or
near-empty file. The newly-rotated path is then re-created empty so the
caller can immediately re-open it for the new run.

This module is deliberately not a logging.Handler subclass: llauncher
does not own the file descriptor at write time (the child process
does), so rotation has to happen between runs.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def rotate_if_needed(
    path: Path,
    *,
    max_bytes: int,
    keep: int,
) -> bool:
    """Rotate ``path`` if its current size exceeds ``max_bytes``.

    On rotation the existing files shift up by one suffix:

        ``path``     →  ``path.1``  (any pre-existing ``path.1`` is
        ``path.1``   →  ``path.2``    pushed up; once we exceed
        ``path.2``   →  ``path.3``    ``keep``, the oldest is removed)

    After rotation the original ``path`` does *not* exist on disk; the
    caller is expected to open it (in append mode) which will create it
    fresh.

    Args:
        path: The active log file. Must end in ``.log`` by convention,
            though the implementation does not enforce a suffix.
        max_bytes: Trigger threshold. ``<= 0`` disables rotation
            entirely (the function returns ``False`` without touching
            the filesystem). Useful for tests.
        keep: Number of rotated files to retain (so the on-disk set is
            ``path``, ``path.1``, …, ``path.{keep}``). ``keep < 1`` is
            clamped to 0, meaning "rotate but keep no history" — the
            current file is removed outright.

    Returns:
        ``True`` if a rotation was performed; ``False`` if not (file
        absent, file under threshold, or ``max_bytes <= 0``).
    """
    if max_bytes <= 0:
        return False

    if not path.exists():
        return False

    try:
        size = path.stat().st_size
    except OSError as exc:
        logger.warning("Could not stat log file %s for rotation: %s", path, exc)
        return False

    if size <= max_bytes:
        return False

    keep = max(0, keep)

    # Bail-on-first-failure rotation. The naive "best-effort, continue
    # on each error" approach can partially commit a chain shift —
    # leaving a permanent gap (e.g., ``.log.2`` becomes ``.log.3`` but
    # ``.log.1`` fails to become ``.log.2``). Subsequent rotations then
    # evict slot-keep contents prematurely because the chain is no
    # longer contiguous. Instead, on any rename or unlink failure, we
    # log a warning and return False without moving the live file. The
    # live file remains at ``path`` and the next start will retry.
    #
    # Walk from the oldest kept slot down to slot 1 so we never overwrite
    # a file we still need to move.
    for n in range(keep, 0, -1):
        src = path.with_suffix(path.suffix + f".{n}")
        if not src.exists():
            continue
        if n == keep:
            # Oldest kept slot — past this would exceed retention; drop it.
            try:
                src.unlink()
            except OSError as exc:
                logger.warning(
                    "Aborting rotation: could not remove old log %s: %s",
                    src,
                    exc,
                )
                return False
            continue
        dst = path.with_suffix(path.suffix + f".{n + 1}")
        try:
            os.replace(src, dst)
        except OSError as exc:
            logger.warning(
                "Aborting rotation: could not rename %s → %s: %s. "
                "Live file left in place; next start will retry.",
                src,
                dst,
                exc,
            )
            return False

    # Finally, move the live file into slot 1 (or remove it if keep == 0).
    if keep == 0:
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Could not remove live log %s: %s", path, exc)
            return False
    else:
        dst = path.with_suffix(path.suffix + ".1")
        try:
            os.replace(path, dst)
        except OSError as exc:
            logger.warning(
                "Could not rotate live log %s → %s: %s",
                path,
                dst,
                exc,
            )
            return False

    return True
