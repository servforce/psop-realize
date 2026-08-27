import shutil
import subprocess
from pathlib import Path

import pytest


NODE = shutil.which("node")
RUNTIME_HARNESS = Path("tests/js/video_app_runtime_harness.cjs")
FRONTEND_SCRIPT = Path("static/assets/video-app.js")


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend runtime tests")
@pytest.mark.parametrize(
    "scenario",
    [
        "stale-update",
        "reparse-update",
        "late-504",
        "collision-409",
    ],
)
def test_video_upload_polling_runtime_scenarios(scenario):
    result = subprocess.run(
        [NODE, str(RUNTIME_HARNESS), scenario, str(FRONTEND_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"frontend runtime scenario {scenario!r} failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
