"""Operator-initiated read-only probe; prints shape only, never bodies or credentials."""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from abs_manager.adapter import AdapterError, CliAdapter, probe_metadata_search
from abs_manager.config import ROOT, Settings, detect_cli
from abs_manager.diagnostics import provider_failure
from abs_manager.locking import BusyError, operation_lock


def cli_profile():
    # Match .NET's Windows UserProfile known folder, rather than assuming that
    # overriding HOME/USERPROFILE isolates ConfigManager (it does not).
    if sys.platform != "win32":
        return Path.home()
    import ctypes
    import uuid
    from ctypes import wintypes
    folder_id = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("5e6c858f-0e22-4760-9afe-ea3317b67173").bytes_le)
    path = ctypes.c_wchar_p()
    shell = ctypes.windll.shell32.SHGetKnownFolderPath
    shell.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    shell.restype = ctypes.c_long
    if shell(ctypes.byref(folder_id), 0, None, ctypes.byref(path)) != 0:
        raise ValueError("Cannot establish the CLI configuration location; refusing CLI execution.")
    try:
        return Path(path.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(path, ctypes.c_void_p))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-cli", action="store_true",
                        help="Also run the exact metadata search command; requires a clean CLI profile.")
    args = parser.parse_args()
    if args.with_cli and (cli_profile() / ".abs-cli" / "config.json").exists():
        parser.error("CLI config exists. No file was read. Use a clean OS account/Windows Sandbox for --with-cli; "
                     "changing HOME/USERPROFILE does not isolate .NET on Windows. The direct probe needs no CLI profile.")
    server = input("ABS server URL (no credentials or query): ").strip()
    provider = input("Provider [audible]: ").strip() or "audible"
    title = input("Exact title / ASIN used in the web UI: ").strip()
    author = input("Exact author filter (blank if none): ").strip()
    token = getpass.getpass("ABS API key (not saved): ")
    settings = Settings(server, token, detect_cli(), timeout=90)
    query = {"title": title, **({"author": author} if author else {})}
    with operation_lock(os.getenv("ABS_MM_DB", str(ROOT / "data" / "metadata.sqlite3"))):
        print(json.dumps(probe_metadata_search(settings, provider, query), sort_keys=True))
        if args.with_cli:
            try:
                command = ["metadata", "search", "--provider", provider, "--title", title]
                if author:
                    command += ["--author", author]
                rows = CliAdapter(settings)._run(command)
                if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                    raise AdapterError("Unsupported CLI candidate schema.", {"category": "invalid_schema", "exit_code": 0})
                print(json.dumps({"transport": "cli", "exit_code": 0, "kind": "valid_candidate_array", "count": len(rows)}))
            except AdapterError as exc:
                print(json.dumps(provider_failure(exc, "selected_provider", "metadata search"), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, EOFError, BusyError):
        print("Diagnostic could not run. Check inputs, CLI installation and terminal access; details withheld.")
        raise SystemExit(1) from None
