# Copyright (C) 2026 Conductor contributors
#
# Conductor Main is free software: you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Conductor Main is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public
# License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Conductor Main. If not, see <https://www.gnu.org/licenses/>.

import os
import subprocess
import sys
from pathlib import Path


def test_gradio_client_does_not_import_legacy_application_modules():
    package_root = Path(__file__).parents[1] / "src" / "conductor_main"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package_root.glob("*.py"))

    assert "from src" not in source
    assert "import src" not in source
    assert "from evaluation" not in source
    assert "import evaluation" not in source


def test_gradio_client_imports_without_loading_legacy_modules():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import conductor_main.app, sys; "
                "print({'src': 'src' in sys.modules, "
                "'evaluation': 'evaluation' in sys.modules})"
            ),
        ],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert completed.stdout.strip() == "{'src': False, 'evaluation': False}"
