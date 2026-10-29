"""Installed-package development scorer entry point; does not run on import."""

import sys

from rfp_matrix.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["eval", *sys.argv[1:]]))
