"""Generate a varied shortlist of numeric .xyz candidates without network access."""

import argparse
import csv
import hashlib
import os
import sqlite3
import sys
import time
from collections import deque
from datetime import date, timedelta
from itertools import chain, islice
from pathlib import Path


PATTERNS = ("repeat", "palindrome", "sequence", "pair", "round", "chunks", "date")
DEFAULT_PATTERNS = PATTERNS[:-1]


def round_robin(iterables):
    """Take turns across finite iterables, removing exhausted ones."""
    pending = deque(map(iter, iterables))
    while pending:
        current = pending.popleft()
        try:
            yield next(current)
        except StopIteration:
            continue
        pending.append(current)


def labels(pattern, length, start=None, end=None):
    """Construct one family directly, preserving all leading zeros."""
    if pattern == "repeat":
        for width in range(1, length):
            if length % width == 0:
                for number in range(10**width):
                    yield f"{number:0{width}d}" * (length // width)
    elif pattern == "palindrome":
        width = (length + 1) // 2
        for number in range(10**width):
            half = f"{number:0{width}d}"
            yield half + (half[:-1] if length % 2 else half)[::-1]
    elif pattern == "sequence":
        for digits in ("0123456789", "9876543210"):
            for index in range(11 - length):
                yield digits[index:index + length]
    elif pattern == "pair":
        if length % 2 == 0:
            width = length // 2
            for number in range(10**width):
                yield "".join(digit * 2 for digit in f"{number:0{width}d}")
    elif pattern == "round":
        for digit in "123456789":
            yield digit + "0" * (length - 1)
    elif pattern == "chunks":
        for first in "0123456789":
            for second in "0123456789":
                if first != second:
                    for split in range(2, length - 1):
                        yield first * split + second * (length - split)
    elif pattern == "date":
        for offset in range((end - start).days + 1):
            current = start + timedelta(days=offset)
            label = f"{current.year:04d}{current.month:02d}{current.day:02d}"
            yield label if length == 8 else label[2:]


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def digit_filter(value):
    if not value or any(digit not in "0123456789" for digit in value):
        raise argparse.ArgumentTypeError("must contain ASCII digits only")
    return value


def numeric_label(value):
    label = value.strip()
    if label.lower().endswith(".xyz"):
        label = label[:-4]
    if not 6 <= len(label) <= 9 or any(digit not in "0123456789" for digit in label):
        raise ValueError(f"invalid numeric .xyz name: {value!r} (expected 6–9 ASCII digits)")
    return label


def generation_options(command):
    command.add_argument("--length", type=int, choices=range(6, 10), action="append",
                          help="label length; repeat in preference order (default: 6)")
    command.add_argument("--pattern", choices=PATTERNS, action="append",
                          help="family; repeat in turn order (default: all except dates)")
    command.add_argument("--number", action="append", default=[],
                          help="explicit label or .xyz domain; repeat in preference order")
    command.add_argument("--input", type=Path, help="UTF-8 file with one explicit name per line")
    command.add_argument("--prefix", type=digit_filter)
    command.add_argument("--suffix", type=digit_filter)
    command.add_argument("--contains", type=digit_filter)
    command.add_argument("--no-leading-zero", action="store_true")
    command.add_argument("--date-start", type=date.fromisoformat, help="inclusive YYYY-MM-DD")
    command.add_argument("--date-end", type=date.fromisoformat, help="inclusive YYYY-MM-DD")
    command.add_argument("--max-generated", type=positive_int, default=250_000,
                         help="raw construction cap including rejections (default: 250000)")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser(
        "generate", help="generate unchecked candidates locally",
        description="Generate unchecked candidates. Filters narrow selected families only.",
        epilog="Explicit numbers come first; families take turns, favoring fewer distinct digits within each length. "
               "Ranking reflects preferences, not resale value. No network requests are made.",
    )
    generation_options(generate)
    generate.add_argument("--limit", type=positive_int, default=50)
    generate.add_argument("--format", choices=("text", "csv"), default="text")
    build = commands.add_parser(
        "build", help="rank locally and keep only the best candidates in SQLite",
        epilog="No network requests. --replace removes excluded names but preserves observations for retained names.",
    )
    generation_options(build)
    build.add_argument("--keep", dest="limit", type=positive_int, default=1000,
                       help="maximum candidates retained (default: 1000)")
    build.add_argument("--database", type=Path, default=Path("domains.sqlite3"))
    build.add_argument("--replace", action="store_true", help="intentionally replace the candidate selection")
    find = commands.add_parser("find", help="browse the ranked SQLite catalog, without network access")
    find.add_argument("--database", type=Path, default=Path("domains.sqlite3"))
    find.add_argument("--pattern", choices=(*PATTERNS, "explicit"), action="append",
                      help="match any selected family")
    find.add_argument("--prefix", type=digit_filter)
    find.add_argument("--suffix", type=digit_filter)
    find.add_argument("--contains", type=digit_filter)
    find.add_argument("--no-leading-zero", action="store_true")
    find.add_argument("--state", choices=("unchecked", "available", "unavailable", "unknown"))
    find.add_argument("--limit", type=positive_int, default=50)
    find.add_argument("--format", choices=("text", "csv"), default="csv")
    serve = commands.add_parser("serve", help="view the catalog in a local, read-only website")
    serve.add_argument("--database", type=Path, default=Path("domains.sqlite3"))
    serve.add_argument("--port", type=positive_int, default=8765)
    return parser


def prepare(args):
    """Validate all inputs before generating candidates or emitting output."""
    explicit = [numeric_label(value) for value in args.number]
    if args.input is not None:
        with args.input.open(encoding="utf-8-sig") as source:
            for line_number, line in enumerate(source, 1):
                if line.strip():
                    try:
                        explicit.append(numeric_label(line.rstrip("\r\n")))
                    except ValueError as error:
                        raise ValueError(f"{args.input}:{line_number}: {error}") from error
    if args.length and any(len(label) not in args.length for label in explicit):
        raise ValueError("an explicit number conflicts with the selected --length")
    lengths = list(dict.fromkeys(args.length or [6]))
    patterns = list(dict.fromkeys(args.pattern if args.pattern is not None else (
        [] if args.number or args.input is not None else DEFAULT_PATTERNS
    )))
    if "date" in patterns:
        if args.date_start is None or args.date_end is None:
            raise ValueError("--pattern date requires --date-start and --date-end")
        if any(length not in (6, 8) for length in lengths):
            raise ValueError("date patterns require length 6 (YYMMDD) or 8 (YYYYMMDD)")
        if args.date_start > args.date_end:
            raise ValueError("--date-start must not be after --date-end")
    elif args.date_start is not None or args.date_end is not None:
        raise ValueError("date bounds require --pattern date")
    if patterns == ["pair"] and all(length % 2 for length in lengths):
        raise ValueError("--pattern pair requires at least one even length")
    return explicit, lengths, patterns


def generate(args, explicit, lengths, patterns):
    """Return (ranked candidates, raw constructions, cap reached)."""
    def tagged(pattern, length):
        for label in labels(pattern, length, args.date_start, args.date_end):
            yield pattern, label

    streams = [tagged(pattern, length) for pattern in patterns for length in lengths]
    pool = chain((("explicit", label) for label in explicit), round_robin(streams))
    reasons = {}
    count = 0
    for count, (pattern, label) in enumerate(islice(pool, args.max_generated), 1):
        if (not label.startswith(args.prefix or "") or not label.endswith(args.suffix or "")
                or (args.contains or "") not in label
                or (args.no_leading_zero and label.startswith("0"))):
            continue
        matches = reasons.setdefault(label, [])
        if pattern not in matches:
            matches.append(pattern)

    length_order = {length: index for index, length in enumerate(lengths)}
    ordered = sorted(
        (label for label, matches in reasons.items() if any(p != "explicit" for p in matches)),
        key=lambda label: (length_order[len(label)], len(set(label)), label),
    )
    family_lists = [[label for label in ordered if pattern in reasons[label]] for pattern in patterns]
    # Shared across iterators: a family's duplicate must not consume its turn.
    seen = set()

    def unseen(candidates):
        for label in candidates:
            if label not in seen:
                seen.add(label)
                yield label

    ranked = chain(
        unseen(label for label in reasons if "explicit" in reasons[label]),
        round_robin([unseen(family) for family in family_lists]),
    )
    rows = [(label, reasons[label]) for label in islice(ranked, args.limit)]
    # At the exact cap, conservatively report possible truncation without
    # constructing an extra candidate solely to detect exhaustion.
    return rows, count, count == args.max_generated


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            import viewer

            if args.port > 65535:
                raise ValueError("--port must be between 1 and 65535")
            viewer.serve(args.database, args.port)
            return 0
        if args.command == "build":
            import catalog

            started = time.perf_counter()
            explicit, lengths, patterns = prepare(args)
            ranked, count, capped = generate(args, explicit, lengths, patterns)
            selection = {
                "lengths": lengths, "patterns": patterns, "keep": args.limit,
                "max_generated": args.max_generated, "prefix": args.prefix,
                "suffix": args.suffix, "contains": args.contains,
                "no_leading_zero": args.no_leading_zero,
                "date_start": str(args.date_start) if args.date_start else None,
                "date_end": str(args.date_end) if args.date_end else None,
                "explicit_count": len(explicit),
                "explicit_digest": hashlib.sha256("\n".join(explicit).encode()).hexdigest(),
            }
            created = catalog.build(args.database, ranked, selection, count, capped, args.replace)
            print(f"{'Built' if created else 'Reused'} catalog: {args.database.resolve()} "
                  f"({len(ranked):,} retained from {count:,} constructions; "
                  f"{time.perf_counter() - started:.2f}s). No availability checks made.", file=sys.stderr)
            if capped:
                print("Generation cap reached; the catalog's candidate pool may be incomplete.", file=sys.stderr)
            return 0
        if args.command == "find":
            import catalog

            rows = catalog.find(args.database, args)
            if args.format == "csv":
                writer = csv.DictWriter(sys.stdout, fieldnames=catalog.COLUMNS, lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
            else:
                for row in rows:
                    print(row["domain"])
            print(f"Found {len(rows):,} candidates in the local catalog.", file=sys.stderr)
            sys.stdout.flush()
            return 0
        explicit, lengths, patterns = prepare(args)
        rows, count, capped = generate(args, explicit, lengths, patterns)
        if args.format == "csv":
            writer = csv.writer(sys.stdout, lineterminator="\n")
            writer.writerow(("domain", "length", "rank", "reasons"))
            for rank, (label, reasons) in enumerate(rows, 1):
                writer.writerow((f"{label}.xyz", len(label), rank, ";".join(reasons)))
        else:
            for label, _ in rows:
                print(f"{label}.xyz")
        if capped:
            print(f"Generation cap reached ({count:,} constructions); pool may be incomplete. "
                  "Use --max-generated to examine more candidates.", file=sys.stderr)
        if not rows:
            print("No candidates match the selected inputs, patterns, and filters.", file=sys.stderr)
        print(f"Examined {count:,} constructions; exported {len(rows):,} unchecked candidates.",
              file=sys.stderr)
        sys.stdout.flush()
    except (ValueError, UnicodeError) as error:
        parser.error(str(error))
    except BrokenPipeError:
        # Avoid a second broken-pipe error during interpreter shutdown.
        sys.stdout = open(os.devnull, "w")
        return 0
    except (OSError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
