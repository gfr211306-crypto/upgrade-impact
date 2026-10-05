"""Run the command-line interface with ``python -m upgrade_impact``."""

import sys

from upgrade_impact.cli import main


if __name__ == "__main__":
    sys.exit(main())
