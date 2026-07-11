import subprocess
import sys
from pathlib import Path


def test_kofax_generator_loads_outside_repo(tmp_path):
    script = Path(__file__).parent / "fixtures" / "generate_kofax_cl_variant.py"
    result = subprocess.run(
        [sys.executable, "-c", "import runpy,sys; runpy.run_path(sys.argv[1])", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
