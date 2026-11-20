"""The core stays headless: no core package imports Qt or the ui package."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

CORE_PACKAGES = (
    "app",
    "camera",
    "pipeline",
    "decision",
    "gateway",
    "analytics",
    "detection",
    "tracking",
)
FORBIDDEN = {"ui", "PySide6", "shiboken6"}


def test_core_ast_has_no_qt_or_ui_imports():
    offenders: list[str] = []
    for package in CORE_PACKAGES:
        for path in Path(package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.split(".")[0] in FORBIDDEN:
                        offenders.append(f"{path}: {name}")
    assert not offenders, "core imports the GUI or Qt: " + "; ".join(offenders)


def test_headless_imports_do_not_load_qt():
    code = (
        "import sys\n"
        "import main\n"
        "import app.controller\n"
        "import app.orchestrator\n"
        "assert 'PySide6' not in sys.modules, 'PySide6 imported headlessly'\n"
        "assert 'ui' not in sys.modules, 'ui imported headlessly'\n"
        "args = main.build_parser().parse_args([])\n"
        "assert args.gui is False\n"
        "main.build_parser().parse_args(['--gui'])\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )
    assert result.returncode == 0, result.stderr
