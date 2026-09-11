"""Keep a ranked shortlist in SQLite; candidate construction stays in memory."""

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


RANKING_VERSION = "pattern-shortlist-v1"
OBSERVATIONS = ("availability", "checked_at", "provider", "registration_price", "renewal_price", "currency")
COLUMNS = ("domain", "length", "rank", "reasons", *OBSERVATIONS)
SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE domains (
    domain TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE CHECK(label NOT GLOB '*[^0-9]*'),
    length INTEGER NOT NULL CHECK(length BETWEEN 6 AND 9 AND length = length(label)),
    rank INTEGER NOT NULL CHECK(rank > 0),
    reasons TEXT NOT NULL,
    availability TEXT NOT NULL DEFAULT 'unchecked'
        CHECK(availability IN ('unchecked', 'available', 'unavailable', 'unknown')),
    checked_at TEXT,
    provider TEXT,
    registration_price TEXT,
    renewal_price TEXT,
    currency TEXT,
    CHECK(domain = label || '.xyz')
);
CREATE UNIQUE INDEX domains_rank ON domains(rank);
"""


def open_catalog(path):
    """Open read-only so a mistyped path never creates an empty database."""
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        if metadata.get("ranking_version") != RANKING_VERSION:
            raise ValueError("Unsupported catalog version; choose a new --database")
        if int(metadata.get("row_count", -1)) != connection.execute("SELECT COUNT(*) FROM domains").fetchone()[0]:
            raise ValueError("Incomplete catalog; choose a new --database")
    except BaseException:
        connection.close()
        raise
    return connection


def build(path, ranked, selection, examined, capped, replace=False):
    """Commit only retained names; publish atomically and preserve retained observations."""
    path = Path(path)
    settings = json.dumps(selection, sort_keys=True)
    retained = {f"{label}.xyz" for label, _ in ranked}
    observations = {}
    exists = path.exists()
    if exists:
        with closing(open_catalog(path)) as connection:
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            if metadata.get("selection") == settings and not replace:
                return False
            if not replace:
                raise ValueError("Catalog has different selection settings; use --replace or another --database")
            for domain, *values in connection.execute("SELECT domain, " + ", ".join(OBSERVATIONS) + " FROM domains"):
                if domain in retained:
                    observations[domain] = values

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".catalog-", suffix=".sqlite3", dir=path.parent)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.executescript(SCHEMA)
            with connection:
                connection.executemany(
                    "INSERT INTO domains VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ((f"{label}.xyz", label, len(label), rank, ";".join(reasons),
                      *observations.get(f"{label}.xyz", ("unchecked", None, None, None, None, None)))
                     for rank, (label, reasons) in enumerate(ranked, 1)),
                )
                connection.executemany("INSERT INTO metadata VALUES (?, ?)", [
                    ("ranking_version", RANKING_VERSION), ("selection", settings),
                    ("row_count", str(len(ranked))), ("examined", str(examined)),
                    ("cap_reached", str(capped).lower()),
                    ("created_at", datetime.now(timezone.utc).isoformat()),
                ])
        if exists:
            os.replace(temporary, path)
        else:
            # Refuse to overwrite a concurrent build that appeared since the check.
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def find(path, args):
    clauses, parameters = [], []
    if args.pattern:
        patterns = list(dict.fromkeys(args.pattern))
        clauses.append("(" + " OR ".join("instr(';' || reasons || ';', ?) > 0" for _ in patterns) + ")")
        parameters.extend(f";{pattern};" for pattern in patterns)
    for value, clause, parameter in (
        (args.prefix, "label LIKE ?", f"{args.prefix}%"),
        (args.suffix, "label LIKE ?", f"%{args.suffix}"),
        (args.contains, "instr(label, ?) > 0", args.contains),
    ):
        if value:
            clauses.append(clause)
            parameters.append(parameter)
    if args.no_leading_zero:
        clauses.append("label NOT LIKE '0%'")
    if args.state:
        clauses.append("availability = ?")
        parameters.append(args.state)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    query = "SELECT " + ", ".join(COLUMNS) + " FROM domains" + where + " ORDER BY rank LIMIT ?"
    with closing(open_catalog(path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, [*parameters, args.limit])]
