"""Convenience launcher so you can run the CLI without `-m`.

    python run.py serve --project config/project.example.yaml
    python run.py run   --project config/project.example.yaml --mock
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from auditfast.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
