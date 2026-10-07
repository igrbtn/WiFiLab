"""Browser-side pure helpers run with node (skipped when node is not installed)."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).parent / "js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


@pytest.mark.parametrize("script", sorted(p.name for p in JS.glob("*_test.mjs")))
def test_js(script):
    r = subprocess.run([NODE, str(JS / script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"
