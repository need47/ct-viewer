"""Convenience launcher for running the hierarchy tree CLI from source checkout."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ct_viewer import main

if __name__ == "__main__":
    main()
