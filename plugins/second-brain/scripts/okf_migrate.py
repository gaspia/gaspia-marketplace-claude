# /// script
# requires-python = ">=3.14"
# dependencies = ["ruamel.yaml>=0.18"]
# ///
"""Entry point for the okf-migrate skill."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from okfmig.cli import main

if __name__ == "__main__":
    sys.exit(main())
