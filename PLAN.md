# Implementation plan

## Objective and boundaries

Build a small CLI that helps a person find interesting numeric `.xyz` domains
they could register at an acceptable registration and renewal price.

Success means a useful shortlist appears quickly, its ranking is understandable,
and only selected candidates incur network work. Complete namespace coverage is
not a success criterion. The previous README recommended 0.01 requests/second;
one million requests at that rate take approximately 3.17 years before retries.
Changing Python versions or increasing concurrency cannot fix that workload.

This reset contains only `README.md` and `PLAN.md`. The previous tracked files
and history are preserved on `backup` at commit
`436747b60724f8cbb1205b6fecbf9b7afbbd5f2d`. The generated local virtual environment
is disposable and is not part of the backup branch.

Deliver the offline generator first. Registrar automation and persistent checks
are subsequent milestones, justified by repeated use. Do not add a web app,
background service, exhaustive crawler, zone-file ingestion, proxy rotation,
multiple registrar integrations, automatic purchases, or a ranking framework.

## Product decisions

- Support numeric labels of lengths 6 through 9; default to length 6.
- Treat labels as ASCII digit strings everywhere. Preserve leading zeros and
  reject Unicode digits, whitespace inside labels, and other suffixes.
- Generate candidates independently of the historical CSV. Old omissions must
  not prevent newly available names from being considered.
- Keep candidate selection entirely offline and deterministic.
- Use explainable pattern priorities, not a purported universal desirability
  score. Do not claim that a simple pattern has investment value.
- Offer plaintext export for a registrar bulk-search box and CSV for inspection.
- Use the intended purchase registrar as the authority for an automated check.
- Never turn errors, missing fields, DNS absence, or RDAP absence into a claim
  that a name is purchasable.
- Bound every online run and stop when enough acceptable options are found.

## Candidate generation

Implement plain generator functions returning labels and match reasons. Start
with these sources; generate their structure directly instead of filtering all
10^length combinations.

| Source | Generation rule | Example | Bound or detail |
| --- | --- | --- | --- |
| Explicit numbers | User-supplied labels or `.xyz` names | `001234.xyz` | Preserve input order; validate every entry |
| Repeated blocks | Repeat a block whose length is a proper divisor of the requested length | `123123.xyz`, `12121212.xyz` | Deduplicate equivalent blocks; include repeated single digits |
| Palindromes | Mirror a half-label, omitting the middle digit for odd lengths | `123321.xyz` | 10^ceil(length/2) constructions; 1,000 at length 6 |
| Sequences | Consecutive ascending or descending digits without wraparound | `456789.xyz` | Do not silently treat 9-to-0 transitions as consecutive |
| Dates | Iterate an explicitly supplied inclusive calendar range | `20260911.xyz` | `YYYYMMDD` at length 8 or `YYMMDD` at length 6; no default date range |

Apply prefix, suffix, and optional exclusion of leading zeros before ranking.
Contradictory filters produce an empty result with an explanation. Validate
requested lengths and incompatible date formats before generation. A five-digit
ZIP code is not silently padded into this class; the user chooses its encoding.

Use `datetime.date` for real dates, including leap days. Require a valid start
and end date and document the ambiguity of two-digit years. Do not infer birth
dates or phone numbers from the user's machine or accounts.

An explicit number receives highest priority. Other families follow the order
the user requests them; the default order is repeated blocks, palindromes, then
sequences. Dates are opt-in. Within a family, prefer the requested length order
and then sort lexically for reproducibility; explicit numbers retain input order.
This default favors small labels within ties; expose the ordering, and do not
describe it as objective quality.

Deduplicate by full domain while collecting all match reasons. A domain keeps
its best priority across matching families and appears only once. Apply the
output limit after deduplication and ranking. Defaults: 50 exported candidates
and at most 100,000 raw constructions per invocation, counting duplicates.
Stop generating at that safety cap, report truncation, and label the result as
the best of the examined pool. Do not claim a global top result for a truncated
pool. Users can deliberately raise the cap for broader searches.

## CLI contract for milestone 1

Use one `xyz.py` script with `argparse`; a package and installed console entry
point are unnecessary. These are proposed implementation commands, not commands
provided by the documentation-only reset:

```sh
uv run xyz.py generate --length 6 --limit 50
uv run xyz.py generate --length 6 --pattern repeat --pattern palindrome --prefix 12
uv run xyz.py generate --number 001234 --number 123123 --format csv
uv run xyz.py generate --length 8 --pattern date --date-start 2026-01-01 --date-end 2026-12-31
uv run xyz.py generate --length 6 --format text > shortlist.txt
```

Allow repeated `--length`, `--pattern`, and `--number` options. Explicit-number
input alone does not implicitly add the default pattern families. If a length
is explicitly selected, reject explicit numbers that contradict it; otherwise
accept their valid 6–9 digit lengths. Add `--input FILE` for one explicit number
or domain per line, using the same validation. Ignore blank lines, but report
invalid entries with line numbers instead of silently dropping them.

Support `--suffix`, `--no-leading-zero`, and `--max-generated`. Accept positive
integer limits only. Plaintext is the default: one domain per line, no header.
CSV columns are `domain,length,rank,reasons`; use the stdlib CSV writer and a
stable delimiter within the reasons field. Never add an availability column to
an unchecked export. Diagnostics and truncation notices go to stderr so stdout
remains usable in a pipeline. No-match is a valid empty result with a diagnostic;
bad arguments exit with status 2. Broken pipes should not print a traceback.

This milestone is useful on its own: review the shortlist, paste it into the
registrar's bulk search, inspect both first-year and renewal costs, and purchase
there if desired. No API key or database is needed.

## Registrar checks: milestone 2

Before implementing an integration, establish which registrar the user intends
to purchase through and whether their account has API access. This is the only
external dependency needed to select the backend; offline work can proceed
without it. Verify current endpoint documentation, account eligibility, batch
size, rate limits, numeric-class pricing, and response examples at that time.

Namecheap is a documented example: `domains.check` accepts up to 50 names and
reports availability and premium prices. Its ordinary registration and renewal
pricing requires the pricing API or checkout verification. This is evidence
that batch checking is possible, not a preselected registrar. Use one concrete
integration function; add no provider interface until a second is required.

Proposed command after the backend exists:

```sh
uv run xyz.py check --input shortlist.txt --target 10 --max-checks 200 --max-requests 50 --timeout 60
```

Keep checking separate from generation: only explicitly exported candidates are
submitted. Process them in their ranked input order. Stop on the first of:

1. The requested number of acceptable results is reached.
2. The input is exhausted.
3. The distinct-name check budget is exhausted.
4. The HTTP request budget is exhausted.
5. The elapsed-time deadline is reached.
6. The user interrupts or the provider reports an account/authentication failure.

`--max-checks` counts distinct names submitted live, including inconclusive
results. `--max-requests` counts every HTTP attempt, including retries and any
pricing calls. Fresh cache hits use neither budget. `--timeout` is the whole-run
deadline measured with a monotonic clock; cap individual request timeouts and
backoff waits by the remaining time. Enforce nonzero bounds.

Start with sequential, provider-sized batches. Limit a batch by the remaining
name budget and remaining target, so a target of 10 does not cause 50 speculative
checks. Respect provider pacing. Retry eligible transient failures at most twice
within the original budgets, honoring `Retry-After` seconds or HTTP dates. If
the required wait exceeds the deadline, end the run with partial results and a
clear reason. Do not sleep for minutes to preserve the illusion of progress.

Keep successful per-domain responses from a partial batch. Reject responses for
unexpected domains, map missing/malformed entries to unknown, and avoid retries
of already successful entries. Authentication/configuration errors end the run
promptly. Timeouts, 429s, and service errors never mean unavailable. Use a
standard HTTP client only if it materially simplifies the chosen API; otherwise
stdlib HTTPS is sufficient for a small sequential workflow. Preserve normal
proxy-environment support and TLS certificate verification.

## Availability and price semantics

| State | Meaning |
| --- | --- |
| `unchecked` | Candidate has no usable registrar observation |
| `available` | Registrar explicitly reported registration availability at the recorded time |
| `unavailable` | Registrar explicitly reported that registration is unavailable |
| `unknown` | A check could not produce a trustworthy answer |

Keep availability separate from price eligibility. An available name with
unknown renewal pricing cannot satisfy a renewal-price ceiling. For online
checks, support optional registration and renewal ceilings in an explicit
currency, exclude premium names by default, and permit an explicit opt-in.
Unknown premium status does not pass the default exclusion rule. Do not convert
currencies silently or treat missing fees as zero. Store prices as decimal
strings or decimal values, never binary floating-point amounts.

Display domain, match reason when supplied, state, provider, checked time,
registration price, renewal price, currency, and premium status. Identify the
registration term and whether fees/taxes are known or excluded. A generic TLD
price is not a verified per-domain numeric-class quote. If a registrar cannot
quote the relevant price, label it unknown and direct the user to checkout.
Even a recent successful check does not reserve a name.

Report stop reason and totals: input names, fresh cache hits, live names checked,
HTTP attempts, available/eligible results, unavailable results, unknown results,
and elapsed time. Exhausted budgets or too few matches are normal outcomes;
fatal provider failures should exit nonzero while preserving partial output.
Define and test these exit semantics in the CLI help when adding `check`.

## Persistence: milestone 3, only for repeated checks

Use the stdlib `sqlite3` module and one local ignored database. No database
server, ORM, queue, worker process, or numeric scan checkpoint is required.
Before adding the database, flush each completed batch to the check output;
users retain partial results even though automatic cache reuse is deferred.

Store the latest observation under `(provider, domain)`, including availability,
UTC check time, registration/renewal prices, currency, registration term,
premium flag, fee coverage, and a concise error code. Missing values remain
null. Ranking and candidate preferences remain separate from cached facts.
If a failed refresh replaces a successful observation, the row becomes unknown;
do not present the earlier answer as newly verified. If the provider supports
multiple quote currencies or terms, reuse only observations matching the current
request; do not compare incompatible quotes.

Initial freshness policy: reuse successful observations for at most 15 minutes;
unknown results are never reusable as a completed check. Make freshness an
explicit run option and provide `--refresh` to force live checks. The TTL is a
request-saving policy, not a promise of continued availability or price.
Commit each completed batch before moving to the next one. Rerunning the same
input skips fresh completed observations and revisits unknown/stale entries.
Budget-limited runs must not mark unvisited candidates completed.

API credentials come from the chosen provider's environment variables. Exclude
credentials, raw authenticated request URLs, and secret-bearing response bodies
from logs, exports, and the database. API keys never belong in committed config.
All network submissions require the explicit `check` command; generation keeps
the user's candidate ideas local. No registration or purchase endpoint is called.

## Repository and toolchain

Keep the implementation small:

```text
README.md           Reader-facing purpose, workflow, domain context, tooling
PLAN.md             Implementation decisions and acceptance criteria
mise.toml           Python 3.14 selection and uv interpreter binding
pyproject.toml      Python requirement and actual dependencies
uv.lock             Reproducible dependency resolution
xyz.py              CLI, generation, ranking; checking when introduced
test_xyz.py         Focused stdlib unittest checks
.gitignore          Local environments, bytecode, credentials, outputs, database
```

Add implementation files only when their milestone starts. A separate registrar
module is warranted only if it makes the resulting script easier to read.
Do not carry forward the old `requirements.txt` or pinned `aiohttp` dependency.
The offline milestone should need no third-party runtime dependencies.

Use mise for Python 3.14 and the user's existing mise-managed uv. Let uv alone
manage the project's `.venv` and dependencies. Do not add a second virtualenv
manager or duplicate Python pin files. Proposed mise configuration:

```toml
[tools]
python = "3.14"

[env]
UV_PYTHON = { value = "{{ tools.python.path }}", tools = true }
```

Set `requires-python = ">=3.14"` in `pyproject.toml`; validate with Python 3.14.
Use a non-packaged uv project initially. Commit `uv.lock`, including when there
are no external dependencies. Run `mise install`, then `mise exec -- uv sync`;
use `mise exec -- uv run ...` when the shell does not activate mise. Confirm the
resolved interpreter with `uv run python -c 'import sys; print(sys.version)'`.
Do not recreate or reuse the old `venv` directory.

## Delivery sequence and verification

### 1. Offline shortlist

Implement toolchain configuration, candidate generation, deterministic ranking,
filters, and plaintext/CSV output. Verify with `uv run python -m unittest` and
CLI smoke runs. Meaningful cases include preserved leading zeros, rejected
Unicode digits, cross-family deduplication, known palindrome counts, leap-day
handling, invalid date ranges, conflicting filters, and cap/truncation behavior.
Patch network entry points to fail during an offline smoke check so accidental
network work cannot pass unnoticed.

Acceptance: default generation returns at most 50 unique, valid six-digit names
with reproducible ordering, and CSV explains each match. Aim for under one second
after environment setup on the developer's machine; record the measurement and
candidate count. Do not make a flaky wall-clock threshold part of unit tests.
Demonstrate the manual bulk-search workflow before expanding the scope.

### 2. Bounded registrar verification

After selecting a usable registrar, implement its availability/price handling,
strict run budgets, early stopping, and structured output. Test using canned
responses and a fake clock: batch limits, target reached, empty/malformed fields,
unknown prices, currency mismatch, premium exclusion, partial batches, 429 with
both forms of `Retry-After`, deadline expiry, and authentication failure. Assert
that retries and pricing requests consume the request budget.

Acceptance: zero requests after any budget ends; no more live names than the
name budget; no error reported as available or unavailable; no purchase request.
Use a small, explicitly initiated live smoke check only after credentials exist.
Report actual provider timing instead of promising a universal network speed.

### 3. Resume repeated checks

Add SQLite only when repeated checks justify it. Verify fresh cache reuse,
stale/unknown refresh, provider isolation, nullable prices, interruption after a
committed batch, and successful continuation from the same input. A temporary
database is enough for tests. Repeated fresh checks should make zero HTTP calls
when cached results satisfy the requested target.

### 4. Evaluate usefulness

Try several real preference sets. Measure time to the first acceptable option,
requests per acceptable option, and how many candidates the user would actually
consider. Adjust pattern ordering or add a concrete requested pattern based on
that feedback. Add concurrency only if measured latency remains a problem within
the provider's limits. Add more integrations only for a real second registrar.

Keep README content reader-facing. Completion markers, remaining decisions,
test evidence, and implementation progress belong here or in commits. The README
must not depend on a particular milestone being unfinished.

## Sources to recheck at integration time

- [Registry numeric browser](https://gen.xyz/number) and
  [pricing](https://gen.xyz/pricing): class definition and advertised prices.
- [2017 Gandi announcement](https://news.gandi.net/en/2017/06/introducing-the-1-111b-class-of-xyz-domains/):
  historical pricing context from the original documentation.
- [Namecheap domain checks](https://www.namecheap.com/support/api/methods/domains/check/),
  [API access](https://www.namecheap.com/support/api/intro/), and
  [pricing API](https://www.namecheap.com/support/api/methods/users/get-pricing/):
  an example of registrar verification; account eligibility and limits must be
  checked before selecting it.
- [RDAP response specification](https://www.rfc-editor.org/rfc/rfc9083.html) and
  [ICANN DNS publication rules](https://itp.cdn.icann.org/en/files/registry-agreements/net/net-agmt-html-01jul17-en.htm):
  why registration evidence and DNS presence must not be conflated.
- [mise Python integration](https://mise.jdx.dev/lang/python.html#mise-uv) and
  [uv projects](https://docs.astral.sh/uv/guides/projects/): interpreter ownership,
  environment management, and dependency locking.
