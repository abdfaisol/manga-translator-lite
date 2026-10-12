"""Unified launcher: `mgtranslate start [--api] [options]`."""

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mgtranslate", description="MangaTranslator launcher"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    # add_help=False so `mgtranslate start --help` shows the real launcher's help.
    start = sub.add_parser(
        "start", add_help=False, help="Start MangaTranslator (web UI by default)"
    )
    start.add_argument(
        "--api", action="store_true", help="Start the HTTP API instead of the web UI"
    )
    # Everything else (--port, --host, --models, --cpu, ...) goes to the real launcher.
    args, rest = parser.parse_known_args(argv)

    target = "api" if args.api else "app"
    sys.argv = [target, *rest]
    return __import__(target).main()
