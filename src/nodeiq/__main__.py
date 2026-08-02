"""Lets `python -m nodeiq <command>` run the CLI (docs/cli_design.md
Section 3) — the invocation style `README.md` already anticipates.
"""

import sys

from nodeiq.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
