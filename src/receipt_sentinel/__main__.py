"""receipt_sentinel.__main__

Entry point for `python -m receipt_sentinel`; delegates to the CLI.

Status:
    None
Issues:
    None
Todo:
    None
"""

from receipt_sentinel.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
