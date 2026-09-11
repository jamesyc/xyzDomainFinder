# xyzDomainFinder implementation plan

## Goal and design choices

Find interesting numeric `.xyz` domains that a person could register at an
acceptable registration and renewal price. Measure time to a useful choice and
network requests per useful choice.

This plan combines the repository's original redesign plan with the local
reference `~/Downloads/xyzDomainFinder-redesign.md`. These decisions supersede
both drafts and are self-contained; implementation does not require that file.
The original scanner and dataset remain on `backup` at
`436747b60724f8cbb1205b6fecbf9b7afbbd5f2d`.

```text
Preferences → generate patterns locally → rank a varied shortlist
            → manual bulk search OR bounded checks with one registrar
            → review availability, price, and check time → registrar checkout
```

Checking fewer names provides the main speedup; supported batching reduces
requests further. At the old recommended 0.01 requests/second, one million
lookups take about 3.17 years before retries. A newer interpreter and more
concurrency cannot overcome that rate limit.

| Decision | What survives the comparison |
| --- | --- |
| Generation | Construct useful patterns directly; no million-row initialization |
| Candidate quality | Visible match reasons, paired digits, round numbers, and memorable chunks; no arbitrary weighted scores |
| Shortlisting | A small, varied, deterministic list rather than 10,000 checks by default |
| Verification | One purchase registrar, supported batches, strict budgets, actual prices, and honest unknown results |
| Storage | Plaintext/CSV exports first; timestamped SQLite observations when repeated checks justify caching |
| Implementation | A small stdlib CLI on Python 3.14, using mise and uv |

Deliver the offline workflow first. Add a registrar when manual checks become
inconvenient, then caching for repeated use. Each milestone must be useful alone.

## 1. Generate useful candidates offline

Support lengths 6–9, defaulting to 6. Keep labels and domain keys as ASCII digit
strings: leading zeros and length are part of identity. Never use an integer
primary key to represent a domain across lengths.

Use plain generator functions. Construct these families directly instead of
filtering every number in the namespace:

| Family | Construction | Example |
| --- | --- | --- |
| Explicit numbers | User-supplied labels or `.xyz` names, in input order | `001234.xyz` |
| Repeated blocks | Repeat a block whose length is a proper divisor of label length | `888888.xyz`, `123123.xyz`, `121212.xyz` |
| Palindromes | Mirror a half-label, without duplicating the middle digit at odd lengths | `123321.xyz` |
| Sequences | Ascending or descending consecutive digits, without wraparound | `456789.xyz`, `654321.xyz` |
| Paired digits | Double each digit of a half-length string; even lengths only | `112233.xyz` |
| Round numbers | One nonzero digit followed by zeros | `100000.xyz` |
| Repeated-digit chunks | Two distinct digits, each repeated in a contiguous run of at least two positions | `111222.xyz`, `111122.xyz` |
| Dates, opt-in | Iterate an explicit inclusive calendar range | `20260911.xyz` |

Repeated blocks already cover alternation; do not implement a duplicate family.
Palindromes need `10^ceil(length/2)` constructions (1,000 for six digits), pairs
need `10^(length/2)` at even lengths, and chunks need at most
`90 * (length - 3)`. These are bounded local workloads, not a reason to store the
entire namespace.

Dates use `datetime.date`, valid leap days, and explicit start/end dates.
Support `YYYYMMDD` at length 8 and `YYMMDD` at length 6; explain two-digit-year
ambiguity. Reject incompatible date lengths and invalid ranges. Other personally
meaningful date encodings can be supplied as explicit numbers.

Apply prefix, suffix, contains, and optional exclusion of leading zeros locally.
Filters narrow selected families; they never enable an exhaustive search.
Normalize outer whitespace and an optional `.xyz` suffix consistently. Reject
Unicode digits, internal whitespace, invalid lengths, and other suffixes. Do not
pad ZIP codes automatically or infer personal numbers from files or accounts.

## 2. Rank a small, varied shortlist

Use visible reasons and deterministic ordering instead of decimal desirability
scores. Repetition, alternation, and low entropy overlap; adding arbitrary
bonuses for all three does not establish quality or resale value.

Explicit numbers come first in input order. For generated names, take one unique
candidate from each selected family in turn until the output limit is reached.
Default family order: repeat, palindrome, sequence, pair, round, chunks. The
user's pattern order determines turn order; a single selected family produces
only that family. This prevents the first large family from filling every slot.

Within a family, prefer requested length order and then lexical order. Skip
already selected domains and continue to the next unseen candidate. Deduplicate
by full domain and retain all match reasons from the examined pool. Explain that
lexical tie-breaking favors small labels; this is reproducibility, not quality.

Default to 50 results and at most 100,000 raw constructions, including duplicates
and filter rejections. Visit explicit inputs first, then interleave generation
by selected family and length so one large generator cannot starve the others.
Materialize only this bounded pool, deduplicate, order each family, and select
results. Report truncation: a shortlist from a truncated pool is not a globally
optimal ranking. Allow an explicit generation-cap override. Do not silently
expand the pool when registrar checks return unavailable names.

## 3. Offline CLI and exports

Use one `xyz.py` script and `argparse`. These examples specify the intended
interface; the documentation-only reset does not implement them yet:

```sh
uv run xyz.py generate --length 6 --limit 50
uv run xyz.py generate --pattern pair --pattern chunks --format csv
uv run xyz.py generate --pattern repeat --contains 88 --prefix 1
uv run xyz.py generate --number 001234 --number 123123
uv run xyz.py generate --length 8 --pattern date --date-start 2026-01-01 --date-end 2026-12-31
uv run xyz.py generate --length 6 --format text > shortlist.txt
```

Allow repeated `--length`, `--pattern`, and `--number`; `--input FILE` accepts one
explicit label/domain per line. Input-only invocations do not add default pattern
families. Explicit inputs may have any supported length unless a specified length
filter contradicts them; reject that conflict rather than dropping user choices.
Pairs skip odd lengths in mixed requests; reject a pair-only request with no
selected even length. Also support `--suffix`, `--no-leading-zero`, and
`--max-generated`. Limits are positive integers.

Default plaintext output is one domain per line without a header. CSV columns
are `domain,length,rank,reasons`, written with the stdlib CSV writer. Generated
exports make no availability claim. Ignore blank input lines, report invalid
entries with line numbers, and send diagnostics to stderr. No matches yields an
empty export and a diagnostic. Bad arguments exit 2; broken pipes exit quietly.

The first delivery is a shortlist suitable for a registrar bulk-search box,
without an API key or database.

## 4. Bounded checks with one registrar

Establish the intended purchase registrar and usable API access before choosing
an integration. Verify current batch support, eligibility, rate limits,
numeric-class prices, and sample responses. Do not select a provider based on
the Downloads draft's approximate batch-size table. Do not preselect NameSilo or
build five integrations. Offline implementation does not depend on this choice.

One concrete check function and a small result record are sufficient; no abstract
provider interface is needed. Isolate response parsing enough to test examples.
Use stdlib HTTPS if sufficient, adding one HTTP dependency only when it materially
simplifies the API. Preserve proxy-environment support and TLS verification.

```sh
uv run xyz.py check --input shortlist.txt --target 10 --max-checks 200 --max-requests 50 --timeout 60
```

Accept plaintext and the generated CSV, retaining CSV reasons. Validate and
deduplicate before checking, preserve ranked input order, and submit names only
through this explicit command. Default ceilings: 10 eligible results, 200 live
names, 50 HTTP attempts, and 60 seconds. These are limits, not throughput promises.

Stop at target reached, input exhausted, either request/name budget exhausted,
elapsed deadline, interruption, or fatal provider error:

- Count each distinct live name once, including inconclusive checks. Every HTTP
  attempt, retry, and pricing call consumes the HTTP budget. Fresh cache hits
  consume neither budget.
- Start with sequential batches, limited by verified provider capacity, remaining
  name budget, and remaining eligible-result target. Preserve successful entries
  in partial batches; retry only unresolved names.
- Respect provider pacing with a minimum request interval. A semaphore alone does
  not limit request rate. Retry transient failures at most twice, using short
  exponential backoff with bounded jitter when no provider delay is supplied.
- Honor `Retry-After` seconds and HTTP dates. Use a monotonic whole-run deadline;
  clamp request timeouts and waits to remaining time. Stop rather than waiting
  past the deadline, and never issue new requests after a budget ends.
- Authentication/configuration failures stop promptly. Unexpected domains,
  malformed entries, timeouts, 429s, and service failures cannot become available
  or unavailable verdicts. Keep missing entries unknown.

Read credentials from environment variables; redact authenticated URLs and
secret-bearing payloads from logs. Never call registration or purchase endpoints.

## 5. Availability, price, and persistence

| State | Meaning |
| --- | --- |
| `unchecked` | No registrar observation has been made |
| `available` | Registrar explicitly reported registration availability at the recorded time |
| `unavailable` | Registrar explicitly reported registration unavailable at the recorded time |
| `unknown` | An attempted check did not produce a trustworthy answer |

Freshness is separate: an old available observation is stale, not newly verified.
Neither DNS absence nor missing RDAP fields proves registrability. Omit DNS
filters and RDAP fallbacks entirely: they add requests and interpretation without
the registrar's price and purchase eligibility. DNS presence likewise does not
become an unconditional cached registration verdict.

Display provider, UTC check time, prices, currency, registration term, premium
status, and known fee/tax coverage. Store prices as decimals or decimal strings;
missing values are null, not zero. Generic TLD pricing is not a verified quote
for a specific numeric-class domain.

Support optional registration and renewal ceilings in an explicit currency.
Unknown required prices or incompatible currencies/terms cannot satisfy a ceiling;
unknown fee coverage cannot satisfy an all-in ceiling. Exclude premium names by
default, with explicit opt-in; unknown premium status does not pass that default
rule. Keep availability and price eligibility separate. A name may be available
but require checkout to establish an acceptable price.

Output checks as CSV, flushing each completed batch. Send counts and stop reason
to stderr: input names, cache hits, live names, HTTP attempts, available/eligible,
unavailable, unknown, and elapsed time. Exhausted inputs/budgets or too few matches
exit 0; invalid arguments exit 2; fatal provider/local I/O failures exit 1;
interruption exits 130 while retaining completed output where possible. Unknown
per-domain results remain visible even when the overall run exits normally.

Add one ignored SQLite database only for repeated use, using stdlib `sqlite3`.
Key observations by `(provider, domain)` with text domains. Store latest state,
UTC check time, quote fields, and concise error code. Regenerate rankings from
preferences; no candidate inventory, stored scores, feature table, or ORM.

Default successful-result freshness is 15 minutes. Reuse only matching quote
contexts; permit an explicit freshness option and `--refresh`. Unknown results
never count as completed cached checks. A failed refresh becomes unknown rather
than making the previous success appear newly verified. Commit each batch;
rerunning input skips fresh observations and revisits stale/unknown entries.
Never mark unvisited names completed. No resume counter or background refresh
is needed. Recheck the selected name and renewal terms at checkout regardless
of cache age; a cached result never reserves a name.

## 6. Python 3.14, mise, and uv

mise selects Python 3.14 and provides the user's existing uv installation. uv
owns dependencies, `uv.lock`, and `.venv`. Drop the old `requirements.txt`, `venv`,
and pinned `aiohttp`. The offline milestone has no third-party runtime dependency.

Proposed `mise.toml`:

```toml
[tools]
python = "3.14"

[env]
UV_PYTHON = { value = "{{ tools.python.path }}", tools = true }
```

Use a non-packaged uv project with `requires-python = ">=3.14"`; commit its
lockfile even without external dependencies. Avoid a second Python pin file or
virtualenv manager. Run `mise install`, then `mise exec -- uv sync`; use
`mise exec -- uv run ...` if mise is not active in the shell. Verify the runtime
with `uv run python -c 'import sys; print(sys.version)'`.

Start with `xyz.py`, `test_xyz.py`, `mise.toml`, `pyproject.toml`, `uv.lock`, and
`.gitignore`, alongside these documents. Ignore environments, bytecode,
credentials, generated exports, and any database. Split a registrar module out
only when readability warrants it; do not scaffold empty provider directories.

## Delivery and acceptance

### Milestone 1: useful offline shortlist

Implement generators, filters, varied ordering, exports, and tooling. Use a small
stdlib unittest suite for preserved zeros/mixed lengths, invalid digits, known
construction counts, leap days, duplicate reasons, contradictory filters, and
deterministic caps. Verify multiple families appear in default output and
explicit inputs retain priority. Check stdout/stderr behavior and zero network
calls. Run `uv run python -m unittest` and representative CLI smoke commands.

Acceptance: at most 50 unique valid names by default, reproducible ordering, and
reasons in CSV. Aim for a warm offline run under one second on the developer's
machine; record timing and pool size, without a flaky timing test. Review the
shortlist through a manual bulk search before expanding implementation.

### Milestone 2: registrar verification

Choose one usable registrar, then implement parsing, batching, prices, budgets,
and flushed output. Use canned responses and a fake clock to test partial
batches, both Retry-After forms, deadline/request accounting, unknown prices,
premium rules, currency/term mismatches, fatal errors, and exit semantics. Assert
no purchase requests occur. Live smoke checks use a small explicit shortlist
after credentials exist; report measured latency, not predicted bulk throughput.

### Milestone 3: cache when repeated usage warrants it

Add SQLite. Test provider isolation, quote-context matching, zero-request fresh
reuse, stale/unknown refresh, and interruption after a committed batch followed
by successful continuation. Use temporary databases; no database initialization
command is needed.

### Evaluate and stop

Try several real preference sets. Measure time to first acceptable option,
requests per acceptable option, and how many results the user would consider.
Add patterns for concrete preferences, concurrency only for measured latency
within provider limits, and another integration only for an actual second
registrar. Keep README reader-facing and stable; progress and test evidence
belong here or in commits.

## Intentionally discarded or deferred

- Full enumeration, a million-row database, and optional exhaustive online scans:
  unnecessary to find a few names, and increasingly wasteful at longer lengths.
- Weighted entropy/luck scores, scoring plugins, and arbitrary rule systems:
  subjective weights hide reasoning; explicit numbers and simple filters cover
  personal preferences. Near-repetition can wait for a demonstrated use case.
- Regex, digit-sum filters, and general query configuration: no current need.
- Provider framework, EPP, RDAP/DNS adapters, and automatic fallback: unnecessary
  integrations before selecting even one purchase registrar.
- Integer domain identities, feature tables, and bitsets: excessive state or loss
  of length, leading-zero, unknown, and freshness information.
- A 24-hour default cache and background refresh: use short session-oriented
  reuse and explicit rechecks instead.
- Token buckets, worker pools, a separate config system, and web UI: add only
  when an observed requirement outgrows the small CLI.

## References

- Comparison source: `~/Downloads/xyzDomainFinder-redesign.md`.
- [Registry numeric browser](https://gen.xyz/number),
  [pricing](https://gen.xyz/pricing), and
  [historical announcement](https://news.gandi.net/en/2017/06/introducing-the-1-111b-class-of-xyz-domains/).
- [Namecheap checks](https://www.namecheap.com/support/api/methods/domains/check/),
  [API access](https://www.namecheap.com/support/api/intro/), and
  [pricing API](https://www.namecheap.com/support/api/methods/users/get-pricing/):
  examples to recheck if choosing that registrar, not a provider commitment.
- [RDAP specification](https://www.rfc-editor.org/rfc/rfc9083.html) and
  [ICANN DNS rules](https://itp.cdn.icann.org/en/files/registry-agreements/net/net-agmt-html-01jul17-en.htm).
- [mise and uv](https://mise.jdx.dev/lang/python.html#mise-uv) and
  [uv projects](https://docs.astral.sh/uv/guides/projects/).
