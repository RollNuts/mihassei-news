"""One fresh build per process for read-only checks of a dedicated checkout.

Tests that mutate inputs or outputs must keep their own isolated build. This
does not cache media validation or reuse dist from an earlier test process.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_built = False


def ensure_site_build() -> None:
    global _built
    if not _built:
        subprocess.run([sys.executable, str(ROOT / "scripts/build.py")],
                       cwd=ROOT, check=True)
        _built = True
