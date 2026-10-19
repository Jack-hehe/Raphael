"""Frozen entry point; keep stdout ownership in the existing core main function."""

from raphael_core.__main__ import main


if __name__ == "__main__":
    main()
