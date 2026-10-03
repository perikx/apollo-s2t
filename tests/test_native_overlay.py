"""Exercise real Qt controls in an isolated Windows process without hardware/API."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop controls")
def test_native_overlay_interactions(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "tests/native_overlay_smoke.py"), str(tmp_path)],
                            cwd=root, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert "hide/show: PASS" in result.stdout, result.stdout + result.stderr
