#!/usr/bin/env python3
"""
VSAA CLI Tool

A lightweight command-line interface for VSAA archives.

Commands:
    c / create   Create archive
    x / extract  Extract archive
    l / list     List archive contents
    i / info     Show archive statistics
    t / test     Validate archive integrity

Examples:
    python vsaacli.py c mydir backup.vsa
    python vsaacli.py x backup.vsa restore_dir
    python vsaacli.py l backup.vsa
    python vsaacli.py i backup.vsa
    python vsaacli.py t backup.vsa
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import vsaa


# ============================================================
# Helpers
# ============================================================


def fatal(msg: str, code: int = 1):
    print(f"Error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def human_size(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(n)

    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024

    return f"{n} B"


def normalize_command(cmd: str) -> str:
    aliases = {
        "create": "c",
        "extract": "x",
        "list": "l",
        "info": "i",
        "test": "t",
    }

    return aliases.get(cmd, cmd)


# ============================================================
# Commands
# ============================================================


def cmd_create(args):
    if not os.path.exists(args.input):
        fatal(f"Input path does not exist: {args.input}")

    compression = (
        vsaa.CompressionMode.DEFLATE
        if args.compress
        else vsaa.CompressionMode.NONE
    )

    print("Creating archive...")
    print(f"  Input:  {args.input}")
    print(f"  Output: {args.archive}")
    print(f"  Compression: {compression.name}")
    print()

    start = time.time()

    vsaa.create_archive(
        args.archive,
        args.input,
        compression=compression,
        level=args.level,
    )

    elapsed = time.time() - start

    archive_size = os.path.getsize(args.archive)

    print("Done.")
    print(f"Archive Size: {human_size(archive_size)}")
    print(f"Elapsed: {elapsed:.2f} s")



def cmd_extract(args):
    if not os.path.exists(args.archive):
        fatal(f"Archive does not exist: {args.archive}")

    out_dir = args.output or os.getcwd()

    print("Extracting archive...")
    print(f"  Input:  {args.archive}")
    print(f"  Output: {out_dir}")
    print()

    start = time.time()

    with vsaa.VSAAArchive(args.archive) as arc:
        arc.extract_all(out_dir)

    elapsed = time.time() - start

    print("Done.")
    print(f"Elapsed: {elapsed:.2f} s")



def cmd_list(args):
    if not os.path.exists(args.archive):
        fatal(f"Archive does not exist: {args.archive}")

    with vsaa.VSAAArchive(args.archive) as arc:
        files = sorted(arc.list())

        if not files:
            print("Archive is empty.")
            return

        print("Archive contents:")
        print()

        for path in files:
            info = arc.info(path)

            usize = info["usize"]
            csize = info["csize"]
            ratio = (1 - (csize / usize)) * 100 if usize else 0

            print(
                f"{human_size(usize):>10}  "
                f"{human_size(csize):>10}  "
                f"{ratio:>6.1f}%  "
                f"{path}"
            )



def cmd_info(args):
    if not os.path.exists(args.archive):
        fatal(f"Archive does not exist: {args.archive}")

    archive_size = os.path.getsize(args.archive)

    with vsaa.VSAAArchive(args.archive) as arc:
        files = arc.list()

        total_usize = 0
        total_csize = 0
        total_path_bytes = 0

        compression_modes = {}

        for path in files:
            info = arc.info(path)

            total_usize += info["usize"]
            total_csize += info["csize"]
            total_path_bytes += len(path.encode("utf-8"))

            mode = vsaa.CompressionMode(info["comp"]).name
            compression_modes[mode] = compression_modes.get(mode, 0) + 1

        metadata_size = 36 + len(files) * 31 + total_path_bytes

        ratio = (
            (1 - (total_csize / total_usize)) * 100
            if total_usize
            else 0
        )

        print("VSAA Archive Information")
        print("=" * 40)
        print()

        print(f"Archive:              {args.archive}")
        print(f"Archive Size:         {human_size(archive_size)}")
        print(f"Files:                {len(files)}")
        print()

        print(f"Original Size:        {human_size(total_usize)}")
        print(f"Compressed Data:      {human_size(total_csize)}")
        print(f"Metadata Overhead:    {human_size(metadata_size)}")
        print(f"Compression Ratio:    {ratio:.2f}%")
        print()

        print("Compression Modes:")
        for mode, count in compression_modes.items():
            print(f"  {mode:<12} {count}")



def cmd_test(args):
    if not os.path.exists(args.archive):
        fatal(f"Archive does not exist: {args.archive}")

    print("Testing archive integrity...")
    print()

    start = time.time()

    tested = 0
    total_bytes = 0

    with vsaa.VSAAArchive(args.archive) as arc:
        files = sorted(arc.list())

        for path in files:
            info = arc.info(path)

            try:
                for chunk in arc.read_stream(path):
                    total_bytes += len(chunk)

                tested += 1

                if args.verbose:
                    print(f"[OK] {path}")

            except Exception as e:
                print(f"[FAIL] {path}: {e}")
                raise

    elapsed = time.time() - start

    print()
    print("Integrity test passed.")
    print(f"Files Tested: {tested}")
    print(f"Bytes Verified: {human_size(total_bytes)}")
    print(f"Elapsed: {elapsed:.2f} s")


# ============================================================
# Parser
# ============================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vsaacli",
        description="VSAA command-line archive manager",
    )

    subparsers = parser.add_subparsers(dest="command")

    # ----------------------------
    # Create
    # ----------------------------

    p_create = subparsers.add_parser(
        "c",
        help="Create archive",
    )

    p_create.add_argument("input")
    p_create.add_argument("archive")

    p_create.add_argument(
        "--compress",
        action="store_true",
        help="Enable DEFLATE compression",
    )

    p_create.add_argument(
        "--level",
        type=int,
        default=6,
        help="Compression level (0-9)",
    )

    # ----------------------------
    # Extract
    # ----------------------------

    p_extract = subparsers.add_parser(
        "x",
        help="Extract archive",
    )

    p_extract.add_argument("archive")
    p_extract.add_argument("output", nargs="?")

    # ----------------------------
    # List
    # ----------------------------

    p_list = subparsers.add_parser(
        "l",
        help="List contents",
    )

    p_list.add_argument("archive")

    # ----------------------------
    # Info
    # ----------------------------

    p_info = subparsers.add_parser(
        "i",
        help="Show archive statistics",
    )

    p_info.add_argument("archive")

    # ----------------------------
    # Test
    # ----------------------------

    p_test = subparsers.add_parser(
        "t",
        help="Validate archive integrity",
    )

    p_test.add_argument("archive")

    p_test.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print every verified file",
    )

    return parser


# ============================================================
# Entry
# ============================================================


def main():
    parser = build_parser()

    if len(sys.argv) <= 1:
        parser.print_help()
        return

    argv = sys.argv[1:]
    argv[0] = normalize_command(argv[0])

    args = parser.parse_args(argv)

    commands = {
        "c": cmd_create,
        "x": cmd_extract,
        "l": cmd_list,
        "i": cmd_info,
        "t": cmd_test,
    }

    if args.command not in commands:
        parser.print_help()
        return

    try:
        commands[args.command](args)

    except KeyboardInterrupt:
        fatal("Interrupted by user", 130)

    except Exception as e:
        fatal(str(e))


if __name__ == "__main__":
    main()
