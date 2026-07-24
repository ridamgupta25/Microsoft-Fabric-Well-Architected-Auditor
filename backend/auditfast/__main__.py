"""Enable `py -m auditfast ...` from the auditfast-core directory."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
