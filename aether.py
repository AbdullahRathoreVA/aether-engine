#!/usr/bin/env python3
"""Launcher. Run it from the project folder you are working in:

    python /path/to/aether-engine/aether.py status

The folder you run it from is the workspace: aether.json, the .aether/ state
folder and the generated site all stay there, never inside the skill folder.
Set AETHER_HOME to use a different workspace.
"""
import os
import sys
from pathlib import Path

if sys.version_info < (3, 10):
    sys.exit('{"error": "Python 3.10 or newer is required"}')

os.environ.setdefault("AETHER_HOME", os.getcwd())
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.cli import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
