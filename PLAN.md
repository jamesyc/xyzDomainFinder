# xyzDomainFinder implementation plan

## Current strategy

Rank candidates locally and persist only the best ones in SQLite. Browse and
refine that catalog before adding any Namecheap requests.

This supersedes the earlier million-row catalog and CSV-first designs, including
`~/Downloads/xyzDomainFinder-redesign.md`. The original scanner remains on
`backup`. Namecheap is the selected future registrar; live checks are deferred
until local candidate quality is useful.

```text
Choose lengths/patterns/personal numbers
  → construct a bounded candidate pool offline
  → deduplicate and rank with visible reasons
  → keep the best K
  → commit only those K to SQLite, initially unchecked
  → search/export/review
  → later: update selected rows with Namecheap observations
```

The same method applies to six-, seven-, eight-, and nine-digit labels. There is
no reason to store every six-digit name simply because a million rows would fit.
There is also no reason to enumerate all longer names to reject most of them.

## What qualifies as highly ranked?

Use an explicit top-K cutoff, defaulting to **1,000 stored candidates per build**.
This is a capacity and preference cutoff, not a claimed market-value threshold.
The build stores fewer rows when fewer candidates qualify. Never fill unused
capacity with arbitrary numbers just to reach K.

Only supplied numbers or recognized patterns enter the pool. Under this ranking
policy, unrecognized arbitrary labels would rank below every selected pattern,
so constructing patterns directly avoids work without losing a preferred
patterned candidate. User-supplied numbers may always be included explicitly.
Dates and other personal encodings require explicit input or date bounds.

Default to six digits. Repeated `--length` supports 6–9 and records the user's
length preference order. Do not pretend a capped or pattern-restricted build is
an exhaustive ranking of the entire namespace.

## Candidate construction and ranking

Keep domains and labels as ASCII strings, including leading zeros. Validate
lengths and suffixes; do not convert domain identity into an integer. Normalize
outer whitespace and an optional `.xyz` suffix. Reject internal whitespace,
Unicode digits, and incompatible explicit-input lengths before writing anything.

| Family | Construction | Example |
| --- | --- | --- |
| Explicit | Supplied labels or domains in preference order | `001234.xyz` |
| Repeat | Repeat blocks whose width divides label length; includes single digits and alternation | `888888.xyz`, `121212.xyz` |
| Palindrome | Mirror half a label without duplicating the odd middle digit | `123321.xyz` |
| Sequence | Ascending/descending consecutive digits, without wraparound | `654321.xyz` |
| Pair | Double each digit of a half-label, even lengths only | `112233.xyz` |
| Round | One nonzero digit followed by zeros | `100000.xyz` |
| Chunks | Two distinct repeated-digit runs, each at least two digits long | `111222.xyz` |
| Date, opt-in | Inclusive calendar interval using YYYYMMDD or YYMMDD | `20260911.xyz` |

Use `datetime.date` for valid dates and leap days. Dates require lengths 8 or 6
respectively; reject unsupported date lengths and invalid ranges. Five-digit ZIP
codes and alternative date encodings must be deliberately encoded by the user.

Apply prefix, suffix, contains, and optional leading-zero exclusion before
ranking. These filters only narrow the constructed pool. Explicit-only input
does not silently add generated families. Combine duplicate match reasons into
one candidate.

Ranking rules:

1. Explicit inputs first, preserving their input order.
2. Generated families take turns, in the selected pattern order. The default
   order is repeat, palindrome, sequence, pair, round, chunks.
3. Within each family, prefer the requested length order, then fewer distinct
   digits, then lexical order. This favors simple structures without arbitrary
   weighted scores. It remains a heuristic, not a valuation.
4. Skip duplicates without consuming a family's turn, and stop after K results.

Bound raw constructions at **250,000** by default, including duplicates and
filter rejections. Explicit inputs come first; generated families/lengths are
interleaved so a large family cannot starve the others before the cap. This cap
covers the default built-in families across all supported lengths; large date
ranges or explicit inputs can still exceed it. Allow an explicit override.
At the exact cap, conservatively report that the pool may be incomplete without
constructing another candidate merely to test exhaustion. Save that fact in
catalog metadata.

## SQLite is the working catalog

Store only retained candidates, never the full rejected pool. Use stdlib
`sqlite3`, without an ORM or database server. SQLite is local and ignored by Git;
CSV and plaintext are exports. Here, “commit candidates” means a SQLite
transaction, not checking generated data into source control.

The `domains` table contains:

- `domain`: text primary key, with a unique numeric `label` and its length.
- `rank`: unique position within this build's retained selection, starting at 1.
- `reasons`: the recognized/supplied reasons behind inclusion.
- `availability`: initially `unchecked`; later `available`, `unavailable`, or
  `unknown` based on explicit registrar observations.
- Nullable `checked_at`, `provider`, `registration_price`, `renewal_price`, and
  `currency`, reserved for the later verification stage.

The `metadata` table records ranking version, selection options, creation time,
raw constructions examined, rows retained, and whether the cap was reached.
Unknown observation/pricing fields stay null; blank is never zero or available.
Create a rank index for fast top-candidate queries.

Build in a temporary SQLite file with a transaction, then atomically publish the
completed file. Failure or interruption must leave the previous catalog intact.
A normal build never overwrites an existing database. Reusing identical settings
is a no-op; different settings require a different `--database` or explicit
`--replace`. Replacement keeps existing observation columns for retained domains;
rows outside the new top K are removed. Make that replacement behavior explicit
in CLI help. Do not implement background jobs or build-history tables.

## Commands and user flow

```sh
mise install
mise exec -- uv sync

# Rank offline and retain at most 1,000 six-digit candidates.
uv run xyz.py build

# Browse rank and match reasons from SQLite.
uv run xyz.py find --limit 20
uv run xyz.py find --pattern palindrome --contains 88
uv run xyz.py find --prefix 12 --no-leading-zero --format text > shortlist.txt

# Deliberately replace the selection with a different length/pattern preference.
uv run xyz.py build --length 8 --length 6 --pattern repeat --pattern palindrome --keep 500 --replace

# A transient shortlist remains useful for experimenting without changing SQLite.
uv run xyz.py generate --length 9 --pattern palindrome --limit 20 --format csv
```

Both generation commands share input validation, patterns, filters, dates, and
ranking. `generate` defaults to 50 rows in plaintext; `build` defaults to keeping
1,000 rows in `domains.sqlite3`. Neither makes network requests. The `find`
command searches stored candidates only: changing its filters cannot discover
names excluded from the build. Multiple find patterns match any selected family.
Stored rank remains the original build rank after filtering.

`find` defaults to CSV with domain, length, rank, reasons, availability, and the
nullable observation columns. Plaintext contains only one domain per line for a
registrar bulk-search box. Diagnostics and summaries go to stderr. No matches
is a normal empty result. Input mistakes exit 2, I/O/database failures exit 1,
interruptions exit 130, and broken pipes exit quietly. Missing database paths
must not create empty SQLite files.

## Namecheap stage: deliberately deferred

No API requests, credential persistence, or check command is part of the current
implementation. First review real shortlists and adjust local preferences.

When requested, add one Namecheap integration that updates selected rows in
place. Do not iterate over every unchecked row automatically. Bound checks by
selected rank range/input, desired results, distinct-name count, HTTP attempts,
and elapsed time. Use verified provider batch sizes and pacing, limited retries,
and Retry-After handling. Save completed batches transactionally.

Keep errors unknown; DNS/RDAP absence is not purchase availability. Record source,
time, currency, term, and known fees with any quote. Do not substitute generic
.xyz TLD prices for numeric-class quotes. Namecheap's current check API documents
premium amounts, but standard domain-specific pricing may still require checkout.
Unknown prices cannot satisfy a corresponding price ceiling.

Successful observations can later be reused for a short explicit TTL, initially
15 minutes. Unknown/stale observations remain eligible for rechecking. Every
purchase requires a final registrar check; this tool never registers domains.
API access requires the user's Namecheap username, key, and allowlisted public
IPv4, supplied through environment variables when this stage is implemented.

## Toolchain and implementation size

Use Python 3.14 selected by mise, with the user's existing mise-managed uv.
uv owns `.venv`, dependencies, and `uv.lock`. Bind `UV_PYTHON` to the mise Python
installation in `mise.toml`; use a non-packaged uv project and no duplicate Python
pin files. No third-party runtime dependencies are needed for this stage.

Keep `xyz.py` for the CLI and shared generation/ranking, `catalog.py` for SQLite,
and `test_xyz.py` for focused stdlib tests. Add only `mise.toml`, `pyproject.toml`,
`uv.lock`, and `.gitignore` as supporting project files. Do not retain the old
requirements, environment, scanner, or provisional unused Namecheap integration.

## Verification and acceptance

1. Verify pattern counts, leading zeros, all supported lengths, explicit-input
   priority, duplicate reasons, date boundaries, filter contradictions, and
   deterministic caps. Default shortlists should contain several families.
2. Build a real catalog. It must contain at most K unique candidates with
   contiguous ranks, correct metadata, and unchecked/null observation fields.
   Rejected candidates must not be inserted. Confirm zero network calls.
3. Test indexed rank browsing, pattern/number filters, preserved leading zeros,
   CSV/plaintext exports, and missing database errors.
4. Test identical-build reuse, refusal to overwrite different selections,
   intentional replacement, preservation of observations for retained domains,
   and rollback on a failed build. Never persist a partially built catalog.
5. Run `uv run python -m unittest` and representative CLI smoke commands. Record
   build time, query time, pool size, retained rows, and database size. Aim for a
   warm default build/query under one second on the developer's machine without
   flaky timing assertions.
6. Update implementation evidence here. Keep the README a reader-facing guide,
   with no milestone status or handoff notes.

## Discarded ideas

No complete numeric namespace in SQLite, exhaustive online scanning, opaque
weighted entropy/luck scores, scoring plugins, provider framework, EPP, DNS/RDAP
fallbacks, bitsets, generic query language, or web UI. Larger candidate retention
limits remain a deliberate user choice; rejected rows do not become background
work merely because they could be stored cheaply.

## Implementation evidence (2026-09-11)

- Implemented `build`, `find`, and `generate`, using only the standard library.
- Verified mise's Python 3.14.7 and created the uv environment and lockfile.
- All 14 unittest cases pass, including atomic replacement failure and retained
  observations, local-only construction, sparse nine-digit storage, and filters.
- Default build: 3,399 raw constructions, 1,000 stored names, 135,168-byte SQLite
  file. An in-process measurement took 0.0303 seconds to build and 0.0187 seconds
  to run a top-20 query; timings describe this machine, not a universal guarantee.
- Created the local ignored `domains.sqlite3`; all 1,000 rows are unchecked and
  SQLite integrity verification returns `ok`. No Namecheap request was made.

## References

- Comparison source: `~/Downloads/xyzDomainFinder-redesign.md`.
- [Registry numeric browser](https://gen.xyz/number), [pricing](https://gen.xyz/pricing),
  and [historical context](https://news.gandi.net/en/2017/06/introducing-the-1-111b-class-of-xyz-domains/).
- [Namecheap checks](https://www.namecheap.com/support/api/methods/domains/check/),
  [pricing](https://www.namecheap.com/support/api/methods/users/get-pricing/), and
  [API access](https://www.namecheap.com/support/api/intro/).
- [mise and uv](https://mise.jdx.dev/lang/python.html#mise-uv),
  [uv projects](https://docs.astral.sh/uv/guides/projects/), and
  [RDAP specification](https://www.rfc-editor.org/rfc/rfc9083.html).
