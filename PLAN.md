# Scored numeric-domain discovery plan

## Implemented: scan all unchecked

[SCAN_PLAN.md](SCAN_PLAN.md) specifies a score-ordered scan of only
`availability = 'unchecked'` rows. Saved domain statuses are the checkpoint:
Cancel keeps completed results, and the next Start runs the same highest-score
unchecked query. No resume button, scan-item table, or persistent cursor is needed.
Known available, unavailable, and unknown rows are excluded even when old.
Only request-rate accounting needs additional durable state. Both checking modes
share a persisted limiter set to 80% of the published limits: 40/minute, 560/hour,
and 6,400/day. The bulk scan starts only through an explicit user action.

## Outcome and latest scope

Implemented a scored collection across 6, 7, 8, and 9 digits. The default retains
up to 10,000 names per length, but this is a configurable browsing/verification
budget, not a quota or an exact full-namespace top-K claim. A minimum score,
selected patterns, or an explicit candidate-work budget can produce fewer rows.

Every stored row includes integer score, rank within its length, and explainable
properties. The website offers length cohorts, minimum-score/property filters,
and a complete point breakdown. Collection and browsing remain offline;
Namecheap verification is now an explicit, bounded `check` command on a selected
catalog subset.

The user's latest clarification supersedes the exactness proof and exhaustive
six-digit oracle proposed below in earlier revisions. We prioritize a broad,
useful candidate collection and correct scoring, retention, and filtering.

## Brainstorm: what makes a number interesting?

Different numbers are memorable for different reasons. The useful signals are
properties we can explain, reproduce, and inspect on an example.

| Idea | Why it might matter | Examples | Decision |
| --- | --- | --- | --- |
| Uniform digits | One digit describes the whole name | `888888`, `7777777` | Strong default signal |
| Repeated blocks | A short motif is easy to remember | `121212`, `123123123` | Strong default signal |
| Mirror symmetry | Visual structure survives reversal | `123321`, `1234321` | Default signal |
| Sequences | Familiar ascending or descending order | `123456`, `987654321` | Strong default signal |
| Counting in chunks | Numbers read as a short counting exercise | `101112`, `123124125` | Default signal |
| Paired digits / repeated runs | Easy to group when reading aloud | `112233`, `111222333` | Default signal |
| Few distinct digits | A smaller alphabet is easier to reconstruct | `8181818`, `100100001` | Modest supporting signal |
| Long zero endings | Round values and simple suffixes | `100000`, `12000000` | Default signal |
| Nearly repeated patterns | A memorable motif with one exception | `111112`, `121213` | Modest default signal |
| Dates | A number can encode recognizable meaning | `20260911`, `240229` | Small default bonus for explicit formats/ranges |
| Mathematical constants | Familiar meaning outside the digit pattern | `314159`, `271828182` | Small curated set; default bonus |
| Personal significance | Birthdays, identifiers, preferred digit strings | User-supplied names | Optional preference bonus later |
| Cultural associations | Some digits matter to particular users | Repeated 8s or a chosen suffix | No universal “lucky” digit bonus |
| Keypad paths / visual rotations | A shape depends on a layout or typeface | Keypad lines, rotated digits | Defer; interpretation is less stable |
| Primes, divisibility, digit sums | Mathematically defined but often hard to notice | Prime labels, multiples of 7 | Defer unless the user values them |
| Random-looking numeric names | May still have personal meaning | Any explicit identifier | No generic bonus; use personal preferences |

Do not add a standalone entropy score on top of digit diversity: that would
mostly reward the same observation twice. Avoid floating-point “98.2” scores
whose precision implies more confidence than the rules justify.

## Scoring v2: additive across independent families

Use nonnegative integer points. For each family, award only its **strongest
matching rule**; then sum the family awards. Record all matched properties,
including weaker matches that earned no additional points.

```text
score = structure + progression + simplicity + roundness + meaning
```

The v2 weights below are explicit preferences. The constant bonus was increased
from 35 to 55 after the first build excluded recognized constants at longer lengths. They are not a measure of availability, price, or resale value.
Do not normalize every length's observed maximum to 100: that would make scores
shift when the candidate pool changes.

### Structure: strongest match, up to 60 points

| Rule | Points | Exact definition |
| --- | ---: | --- |
| Uniform | 60 | Every digit is identical |
| Exact repeated block | 45 | Minimal period is at least 2, divides the full length, and repeats at least twice |
| Palindrome | 35 | The entire label equals its reversal |
| Paired digits | 30 | Even length; each aligned pair consists of the same digit |
| Repeated-digit chunks | 20 | Exactly 2 or 3 maximal constant-digit runs cover the label; each run has length at least 2 |
| Staircase | 30 | At least three maximal runs; both their digit values and lengths form non-wrapping +1 or -1 sequences |
| Near repetition | 20 | Exactly one substitution from a uniform or exact repeating label whose base block is at most 3 digits; its block width must divide the full length |

Minimal period prevents `121212` from being counted as both “alternating” and
several different repeating block patterns. A uniform palindrome earns 60 in
this family, not 60 + 35. Detect and display both properties nonetheless.

### Progression: strongest match, up to 60 points

| Rule | Points | Exact definition |
| --- | ---: | --- |
| Whole-label sequence | 60 | Every adjacent step is +1 or every step is -1; no 9-to-0 wraparound |
| Counting blocks | 45 | Split into equal-width 2- or 3-digit blocks; at least 3 blocks; values progress by +1 or -1 without overflow |
| Stepping pairs | 30 | Paired-digit label whose collapsed sequence has at least 3 digits and progresses by +1 or -1 |
| Repeated sequence | 30 | The minimal repeating motif has at least three digits and is an ascending/descending sequence |
| Mirrored sequence | 30 | A palindrome whose first half, including an odd center, is a sequence of at least three digits |
| Stepping runs | 30 | Three or more consecutive digit-runs, each at least two digits long, or a staircase as defined above |
| Dominant consecutive run | `floor(30 * run_length / length)` | Longest contiguous +1 or -1 run covers at least `ceil(2 * length / 3)` digits |

For counting blocks, preserve zero-padded blocks in the label and evidence.
A whole sequence also contains shorter runs, but those do not stack additional
progression points. Dominant-run coverage uses a proportion so nine-digit names
do not earn easy points merely by having more substring positions.

### Simplicity: strongest match, up to 20 points

| Number of distinct digits | Points |
| --- | ---: |
| 1 | 20 |
| 2 | 14 |
| 3 | 6 |
| 4–10 | 0 |

This deliberately measures alphabet size, separate from arrangement. A two-digit
repeat can earn structure and simplicity points. Document that choice rather
than accidentally double-counting it through multiple entropy-like formulas.

### Roundness: up to 25 points

Award 25 for a trailing run of zeros covering at least
`ceil(2 * length / 3)` positions, with at least one nonzero digit before the run.
The all-zero label does not receive a separate roundness bonus.
Leading zeros remain valid and receive no blanket penalty.

### Meaning: strongest match, up to 55 points

- **Recognized constant, +55:** use the first N digits including the integer part,
  with the decimal point removed. Initial constants: pi, e, and the golden ratio,
  with reviewed literal digit strings for N = 6–9. Store the constant's name and
  the exact source string in the versioned scoring profile.
- **Recognized date, +12:** six digits interpreted as YYMMDD in 2000–2099; eight
  digits as YYYYMMDD in 1900–2099. Require a real calendar date, including valid
  leap-day handling. Seven- and nine-digit labels have no default date encoding.
  Display the format and interpreted date; do not silently assume MMDDYY or DDMMYY.

The fixed date intervals make a build reproducible rather than changing with
today's date. Other ranges/encodings and user-supplied meaningful numbers can be
an explicit later profile option. Do not load personal data automatically.

### Worked examples under the proposed weights

| Label | Awarded contributions | Total |
| --- | --- | ---: |
| `888888` | Uniform 60 + one distinct digit 20 | 80 |
| `121212` | Repeated block 45 + two distinct digits 14 + date (2012-12-12) 12 | 71 |
| `112233` | Paired digits 30 + stepping pairs 30 + three distinct digits 6 | 66 |
| `100000` | Near repetition 20 + two distinct digits 14 + zero ending 25 | 59 |
| `123456` | Whole-label sequence 60 | 60 |
| `123321` | Palindrome 35 + mirrored sequence 30 + three distinct digits 6 | 71 |

These examples are regression cases and discussion material. For instance,
whether `112233` should beat `123456` is a preference to calibrate, not a fact.
Add comparison cases from all four lengths before finalizing the weights.

## What the stored explanation looks like

Store stable machine-readable property IDs plus readable evidence and awarded
points. A property must describe the label, independent of which generator found
it. Always run the same pure scorer over a candidate's complete label.

Example for `888888.xyz`:

```json
{
  "domain": "888888.xyz",
  "length": 6,
  "score": 80,
  "reasons": "uniform;digit_diversity;pair;palindrome",
  "properties": [
    {
      "id": "uniform",
      "family": "structure",
      "title": "Uniform digits",
      "points": 60,
      "awarded": 60,
      "evidence": "8 repeated 6 times"
    },
    {
      "id": "digit_diversity",
      "family": "simplicity",
      "title": "Few distinct digits",
      "points": 20,
      "awarded": 20,
      "evidence": "8"
    },
    {
      "id": "pair",
      "family": "structure",
      "title": "Paired digits",
      "points": 30,
      "awarded": 0,
      "evidence": "88 / 88 / 88"
    },
    {
      "id": "palindrome",
      "family": "structure",
      "title": "Palindrome",
      "points": 35,
      "awarded": 0,
      "evidence": "888888 reads the same in reverse"
    }
  ]
}
```

Sum of `awarded` must equal `score`. A weaker property remains discoverable by
filters even when a stronger match consumed that family's award. Break equal
rule awards within a family by a fixed rule-ID ordering.

## Practical candidate collection

`candidates.py` constructs repeats, palindromes, pairs, chunks, full and dominant
sequences, counting blocks, near repeats, long zero endings, dates, and constants.
It processes a length at a time and uses a bounded heap to retain the strongest
observed candidates. A label always receives the same score regardless of which
constructor emitted it. Heap ties prefer fewer digit-runs, then fewer distinct digits, then lexical label order within a length.

Uniform and one-/two-/three-digit-alphabet generators provide compact-digit
fallbacks for sparse collections. A fallback is skipped when the retained cutoff
already exceeds its standalone simplicity award. This keeps work focused on
structured names; the result is explicitly recorded as a curated, generated
pool rather than a proof that every possible numeric label was ranked.

Only retained heap labels need a deduplication set. With fixed scores and a
monotonically improving cutoff, a rejected/evicted duplicate cannot later beat
the cutoff. Full property explanations are allocated for winners after selection;
the faster scoring path returns just the integer total. Tests compare both paths.

The default has no arbitrary construction cap. `--max-generated` is an optional
per-length work budget, recorded in metadata with a visible warning when reached.
The actual retained count and cutoff are reported per length; never pad a sparse
collection with zero-score arbitrary names. `--min-score 0` is available when
explicit supplied numbers without recognized traits should still be retained.

There is no complete namespace table, random sample presented as exact, or
background queue of rejected names. Broader future coverage can be added through
concrete constructors without changing the storage or query model.

## SQLite changes

Retain the existing `domains` table and observation columns, with a new schema
version. Add:

- `score INTEGER NOT NULL CHECK(score >= 0)`.
- `properties_json TEXT NOT NULL`: the scored properties and evidence.
- A stable compact list of property IDs for backward-compatible reason display
  or generate it from the JSON in one shared read helper; do not let two property
  sources drift independently.
- `rank`: now rank **within a digit length**, not across the whole database.

Replace the global unique rank index with `UNIQUE(length, rank)`. Add an index
on `(length, score DESC, rank)` for ranked length views. A mixed-score index
supports the default all-length website query without sorting the entire catalog.

Build metadata includes schema/scoring versions, resolved profile, default
lengths `[6,7,8,9]`, `keep_per_length=10000`, candidate coverage method, build time,
and per-length row count, raw candidate work, elapsed time, and cutoff score.
The total row count must not conceal which lengths were built or their individual counts.

Publish a new database atomically only after all requested lengths finish and
validation passes. Use the current safe temporary-file workflow. Support the
current v1 schema as an observation source during replacement: preserve status,
provider, timestamps, and prices for retained domains. New rows stay unchecked.
Do not fabricate scores for old rows or reset existing registrar observations.
Existing replacement semantics still apply to candidates that drop out.

The SQLite file remains local and ignored by Git. Commit the scorer, constructors,
code, tests, and documentation; persist only winning domains as database rows.

## CLI changes

Implemented scoring and browsing interface:

```sh
# New defaults: all four lengths, 10,000 winners per length.
uv run xyz.py build --replace

# Explicit subset or alternative per-length retention.
uv run xyz.py build --length 7 --length 9 --keep-per-length 10000 --database selected.sqlite3

# Inspect the score and evidence without building or checking registration.
uv run xyz.py score 888888 112233 123456789

# Browse a specific length and property.
uv run xyz.py find --length 8 --min-score 50 --pattern palindrome --limit 50
uv run xyz.py serve
```

Replace ambiguous build `--keep` with `--keep-per-length`. The old flag should
produce a helpful error rather than silently changing meaning. Repeating lengths
selects cohorts, not a preference that can starve other lengths.

`score` accepts validated six- through nine-digit labels or `.xyz` domains and
prints totals plus property awards/evidence. `find` gains length and minimum-score
filters. Exports include length, score, rank-in-length, properties, and existing
availability/price fields. Ranks are recomputed when weights change; scores and
rank positions are separate concepts.

Keep `generate` for transient experimentation with the same scorer. A filtered
or explicitly capped preview is described as such. Replacing a different existing
catalog requires the explicit `--replace` option.

## Website changes

The broader catalog needs more than adding three options to the existing selector:

- Show **All / 6 digits / 7 digits / 8 digits / 9 digits**, with actual counts.
- Show total saved domains and per-length coverage. A full default build can retain
  40,000 total; display actual counts rather than hardcoding that total.
- Add a score column; label rank as “Rank in length.” Do not imply a global rank
  when the all-length list contains four separate #1 entries.
- Default all-length ordering: score descending, then length ascending, then
  rank ascending. Within a selected length use the stored rank, including the v2 simplicity tie-breaks.
- Add minimum-score filtering and property filters based on scored property IDs.
- In candidate details show total score, each family award, all matched properties,
  and readable evidence. Explain zero-award overlaps as covered by a stronger
  property in the same family.
- Display scoring version and cutoff information in a compact catalog-info area.
- Keep availability separate; unchecked names are never styled as confirmed free.

Move filtering, sorting, counts, and pagination to the Python/SQLite read API.
Forty thousand rows plus explanation JSON should not be downloaded and rendered
as one browser snapshot. Return only the current page and summary counts; fetch
full explanation data when opening a candidate. CSV export streams the full
filtered selection, not merely the visible page. Validate query parameters and
use parameterized SQL with an allowlist for sort expressions.

Reuse the current viewer, HTML/CSS, and local server. No frontend framework,
cloud deployment, database service, or new website backend is needed.

## Implementation structure

1. **Scorer:** `scoring.py` holds versioned rules, fixed constants/date ranges,
   a fast integer scorer, and full property evidence. `score` exposes the breakdown.
2. **Candidate search:** `candidates.py` holds constructors, source filtering,
   and per-length heap selection. Score-first ordering replaces round-robin ranking.
3. **Catalog:** `catalog.py` stores score/properties, independent length ranks,
   and per-length metadata. Atomic rebuild migrates old observations for retained names.
4. **CLI and website:** `xyz.py` exposes collection and filters; `viewer.py` shares
   database query code for bounded pages, single-domain details, and streamed CSV.
   The existing frontend renders only the page rather than loading 40,000 explanations.
5. **Calibration:** inspect examples and property distributions. New weights require
   a scoring-version change and rebuild. Preserve visible reasons rather than
   adding undisclosed ranking adjustments.

Better catalog coverage is not authorization to check all 40,000 names online.
The explicit checking stage below operates on selected names and fixed budgets.

## Selective Namecheap checks

Implemented `check` with the catalog's length, property, score, digit, and state
filters, plus explicit names and plaintext/CSV input. `--preview` lists the
selection without credentials, requests, or writes. Unknown names are rejected
before any request. The website can run the same checker after an exact selection preview; browsing remains read-only.

Default bounds: 200 selected candidates, 50 distinct live names, 20 HTTP attempts,
60 seconds, and a target of 10 eligible available results. Batches are at most
50 names and limited by remaining budgets/target. A single Namecheap client uses
serial pacing, a maximum of two retries, and Retry-After-aware waits. A deadline
also bounds network operations. Authentication/configuration failures stop.

Reuse successful Namecheap observations for 15 minutes unless refreshed explicitly.
Unknown, stale, future-dated, or other-provider results cannot count as fresh
cache hits. Save each response batch transactionally, including partial failures;
retry only unresolved entries. A failed refresh becomes unknown rather than
making an old success appear newly verified.

Observation schema v3 adds nullable premium status, term, reported fees, quote
notes, and error codes. Old catalogs remain readable through null projections;
checking migrates observation columns in place. Rebuilds preserve every retained
observation field. An advisory write lock coordinates checks and atomic rebuild
publication without holding SQLite transactions across network waits.

Live testing showed that Namecheap marks inexpensive numeric-class names as
premium, so exclusion is opt-in (`--exclude-premium`). Unknown currency/term or
price fields cannot satisfy explicit price ceilings. Reported premium amounts
are stored without inventing missing quote context or using generic TLD pricing.

`NAMECHEAP_USERNAME`, `NAMECHEAP_API_KEY`, and `NAMECHEAP_CLIENT_IP` come from the
environment. Neither logs nor SQLite persist credentials or raw authenticated
request URLs. The client calls only `namecheap.domains.check` over HTTPS; it
blocks redirects and never invokes registration/purchase endpoints.
Local connection settings are loaded by mise from an ignored `mise.local.toml`
with owner-only permissions and API-key redaction. That file is not committed.

Namecheap validation covers budgets, cache reuse, partial responses,
malformed results, Retry-After, interruption, redaction, price eligibility,
preview behavior, and write coordination. A one-request live smoke check on
`111111.xyz`, `31415926.xyz`, and `314159265.xyz` succeeded; all three were reported
unavailable and persisted. A repeat run reused all three observations with zero
requests. No broad catalog scan was performed.

## Validation and observed results

- The default build retained 10,000 unique rows in each of the four lengths.
  Lower limits and stricter filters are supported; row count is not a quality metric.
- Worked scores and sampled labels validate that family awards sum to each score
  and that the fast scorer agrees with detailed explanations. Covered properties
  remain visible without double-awarding points.
- Finite-pool tests verify deterministic score/label order and duplicate handling.
  Date, near-repeat, sequence, validation, and leading-zero cases are covered.
- Database tests cover multi-length ranks, JSON explanations, filtered queries,
  same-settings reuse, failed publication rollback, and v1 observation migration.
- HTTP tests cover pagination, score/length/property filters, detail explanations,
  full-filter export independent of page size, invalid parameters, and read-only
  access. No registrar request is made.
- The first full run considered 463,404 raw candidate emissions across lengths
  and took approximately 4.4 seconds of candidate selection on this machine.
  Rejected candidates were not inserted into SQLite. Timings are measurements,
  not universal performance promises.

## Scope guardrails

No learned model, resale-price predictor, opaque fractional score, scoring plugin
framework, full namespace database, random candidate sampling presented as exact,
or registrar work not explicitly started by the user. Add personal/cultural scoring only through
explicit preferences, and add new positive rules with explicit construction and test strategies.

Use the existing mise-selected Python 3.14, uv environment, and standard-library
SQLite/tooling. The original scanner remains preserved on `backup`.

## Quality improvements and completed website workflow

Scoring v2 adds sequence motifs, mirrored sequences, stepping runs, and staircase
run shapes. It increases whole-sequence and counting-block awards, completes
three-run generation, and uses simpler run structures to break score ties.
`QUALITY.md` records measured before/after scores and ranks. The rebuild retained
40,000 names and preserved the three existing Namecheap observations.

The website now supports selecting up to 50 names across pages and filters. A
server-generated preview freezes the exact names, refresh policy, and limits for
five minutes. Starting consumes that preview; duplicate/replayed starts are
rejected. The child CLI checks only those names, with a 50-name maximum, 20
requests, and 60 seconds. Its available-result target equals the selection size,
so it processes the selection rather than silently stopping after ten successes.

`web_checks.py` supervises one child process and retains only its latest status.
CSV results drive progress; stderr is bounded and redacted. Cancellation sends
SIGINT to that child, letting the existing checker save attempted/completed
observations. Server shutdown also stops its owned child. SQLite remains the
persistent source of results; no worker queue or job-history database is added.

Mutating HTTP endpoints require the exact local Host and Origin, JSON bodies,
validated catalog names, and bounded request sizes. Credentials stay server-side.
The frontend restores in-progress status after refresh, automatically refreshes
results, and exposes cancellation and recoverable errors. Ordinary page loads,
filtering, and previews never initiate registrar requests.

Validation covers composed-pattern scoring, false positives, constructor/scorer
alignment, deterministic ties, preview expiry, catalog changes, duplicate starts,
progress, redaction, cancellation, and cross-origin rejection. A real CLI child
was tested using fresh cached observations with credentials disabled; it completed
with zero network requests. No new live Namecheap query was needed for this work.

The current full suite has 30 passing tests. The live catalog passes SQLite integrity and score-sum checks; all three existing observations remain unchanged.

## Unchecked scan and preview-error follow-through

The unchecked-only scan is implemented with cooperative cancellation, per-response
commits, no domain cursor, and a durable account-scoped request ledger. The website
shows scope, progress, request waits, and cancellation; Start always uses the same
unchecked query. Selected checks retain their small-run budgets and share the ledger.

The reported JSON.parse error came from a stale server returning HTML for the new
scan endpoint. Restarting loaded the endpoint; JSON API error responses and a
shared browser parser now provide actionable errors instead of raw parse failures.
Both frontend and HTTP regression tests cover this case.
