"""Installable command line for review and explicitly confirmed writes."""

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from .adapter import AdapterError, HttpAdapter
from .bulk import Bulk, book_libraries
from .files import contains_token, data_directory, identifier
from .locking import BusyError
from .models import compact
from .review import load_approval, review

RESULT_STATES = ("verified", "already_applied", "conflict", "uncertain", "not_attempted")


def positive_count(text):
    count = int(text)
    if count < 1:
        raise argparse.ArgumentTypeError("Use a positive number of books.")
    return count


def redact_error(message):
    token = os.environ.get("ABS_TOKEN", "").strip()
    return message.replace(token, "[redacted]") if token else message


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        super().error(redact_error(message))


def parser():
    cli = ArgumentParser(prog="absmm", description="Review and maintain Audiobookshelf metadata.")
    sub = cli.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="Export a library or discover library IDs.")
    scope = export.add_mutually_exclusive_group()
    scope.add_argument("--library")
    scope.add_argument("--list-libraries", action="store_true")
    review_parser = sub.add_parser("review", help="Generate an offline review from one corrections file.")
    review_parser.add_argument("corrections", type=Path)
    apply = sub.add_parser("apply", help="Preview and confirm an approval file.")
    apply.add_argument("approvals", type=Path)
    apply.add_argument("--limit", type=positive_count, metavar="N",
                       help="Apply up to N selected books in ascending ABS item ID order.")
    check = sub.add_parser("check", help="Reconcile uncertain outcomes without writing to ABS.")
    check.add_argument("--abandon", metavar="OP_ID")
    check.add_argument("--reason")
    return cli


def ask(phrase):
    if not sys.stdin.isatty():
        raise ValueError("Confirmation requires an interactive terminal.")
    print(f"Type {phrase} to continue.", flush=True)
    return input("> ") == phrase


def confirm_apply(plan, phrase):
    print(f"ABS server {plan['server']}")
    print(f"Library {plan['library_id']}")
    print(f"Snapshot {plan['snapshot_id']}")
    for item, fields in plan["items"].items():
        print(f"\n{item}")
        for field, values in fields.items():
            print(f"  {field}")
            print(f"    Before  {compact(values['before'])}")
            print(f"    After   {compact(values['after'])}")
            if values["flags"]:
                print(f"    Review  {', '.join(values['flags'])}")
    return ask(phrase)


def backup():
    print("Confirm you have a current backup of ABS /config and /metadata.")
    return ask("BACKUP CONFIRMED")


def confirm_abandon(data, phrase):
    print(f"Confirm you manually inspected ABS item {data['item_id']} in library {data['library_id']}.")
    return ask(phrase)


def show_results(results, summary=False):
    for identity, state, message in results:
        print(f"{identity}  {state}  {message}")
    if summary:
        counts = Counter(state for _, state, _ in results)
        print("  ".join(f"{state}={counts[state]}" for state in RESULT_STATES))
    return 1 if any(state in {"conflict", "uncertain", "not_attempted"} for _, state, _ in results) else 0


def export_progress(processed, total):
    print(f"Processed {processed}/{total} books.", file=sys.stderr, flush=True)


def run(args, directory):
    token = os.environ.get("ABS_TOKEN", "").strip()
    if args.command == "review":
        report, count = review(directory, args.corrections, token)
        print(f"{count} proposed field changes")
        print(f"Manifest  {report.with_suffix('.json')}")
        print(f"Open in a browser  {report}")
        return 0
    if args.command == "check":
        if args.abandon is not None and (not args.reason or not args.reason.strip()):
            raise ValueError("--abandon requires --reason.")
        if args.abandon is None and args.reason is not None:
            raise ValueError("--reason is only used with --abandon.")
    adapter = HttpAdapter(os.environ.get("ABS_URL", ""), token)
    service = Bulk(adapter, directory)
    library = os.environ.get("ABS_LIBRARY") or None
    if args.command == "export":
        if args.list_libraries:
            for row in book_libraries(adapter):
                print(f"{row['id']}  {row['name']}")
        else:
            print("Checking library membership…", file=sys.stderr, flush=True)
            export, count = service.export(args.library or library, progress=export_progress)
            print(f"{count} books exported")
            print(f"Share with the external reviewer  {export}")
        return 0
    if args.command == "apply":
        snapshot, manifest, selected, limited = load_approval(directory, args.approvals, args.limit, token)
        results = service.apply(snapshot, manifest, selected, limited, library, confirm_apply, backup)
        if not results:
            print("No actionable changes selected.")
        return show_results(results, summary=True)
    if args.abandon is not None:
        if contains_token(args.reason, token):
            raise ValueError("Abandonment reason contains the configured credential.")
        service.abandon(identifier(args.abandon, 32), args.reason, confirm_abandon)
        print(f"{args.abandon}  abandoned")
        return 0
    results = service.check()
    if not results:
        print("No unresolved operations.")
    return show_results(results)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args, data_directory())
    except KeyboardInterrupt:
        print("\nInterrupted. Run absmm check before resubmitting changes.", file=sys.stderr)
        return 130
    except (AdapterError, BusyError, ValueError, OSError, EOFError, TypeError, KeyError) as exc:
        message = redact_error(str(exc) or type(exc).__name__)
        print(f"absmm: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
