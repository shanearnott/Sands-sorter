from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sands-worker")
    parser.add_argument("command", choices=["poll", "summarize"])
    args = parser.parse_args(argv)

    if args.command == "poll":
        print("poll: not yet implemented (M2)")
        return 0
    if args.command == "summarize":
        print("summarize: not yet implemented (M3)")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
