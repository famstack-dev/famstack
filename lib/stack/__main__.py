"""Entry point for python3 -m stack."""
import sys

if sys.version_info < (3, 9):
    sys.exit("famstack requires Python 3.9+.")

from .cli import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        import sys
        print()
        sys.exit(0)
