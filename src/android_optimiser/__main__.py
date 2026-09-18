"""Entry point for ``python -m android_optimiser``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
