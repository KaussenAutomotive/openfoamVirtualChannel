import sys

from .ui.main_window import run


def main() -> int:
    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
