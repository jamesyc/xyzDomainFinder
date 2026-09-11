# Scored numeric-domain discovery plan

## Outcome

Build one local SQLite catalog containing the **10,000 highest-scoring domains
for each of 6, 7, 8, and 9 digits**: 40,000 rows in the default build.
Each row carries an integer score, its rank within that length, and the properties
that explain its score. The website exposes all four lengths and the breakdown.

This is a plan for the next implementation. The current website reads a
1,000-row six-digit catalog because that was the previous build default. It has
pattern reasons and an ordinal rank, but no additive scoring system. This plan
replaces the family-round-robin ranking and single global retention limit.
The current database remains untouched during planning.

All scoring and catalog construction remain offline. Namecheap verification is a
later action on selected candidates, not part of this catalog build.

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

## Scoring v1: additive across independent families

Use nonnegative integer points. For each family, award only its **strongest
matching rule**; then sum the family awards. Record all matched properties,
including weaker matches that earned no additional points.

```text
score = structure + progression + simplicity + roundness + meaning
```

The initial weights below are an explicit preference hypothesis. Review examples
before freezing v1. They are not a measure of availability, price, or resale value.
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
| Near repetition | 20 | Exactly one substitution from a uniform or exact repeating label whose base block is at most 3 digits; its block width must divide the full length |

Minimal period prevents `121212` from being counted as both “alternating” and
several different repeating block patterns. A uniform palindrome earns 60 in
this family, not 60 + 35. Detect and display both properties nonetheless.

### Progression: strongest match, up to 45 points

| Rule | Points | Exact definition |
| --- | ---: | --- |
| Whole-label sequence | 45 | Every adjacent step is +1 or every step is -1; no 9-to-0 wraparound |
| Counting blocks | 35 | Split into equal-width 2- or 3-digit blocks; at least 3 blocks; values progress by +1 or -1 without overflow |
| Stepping pairs | 30 | Paired-digit label whose collapsed sequence has at least 3 digits and progresses by +1 or -1 |
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

### Meaning: strongest match, up to 35 points

- **Recognized constant, +35:** use the first N digits including the integer part,
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
| `123456` | Whole-label sequence 45 | 45 |
| `123321` | Palindrome 35 + three distinct digits 6 | 41 |

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
  "properties": [
    {"id": "uniform", "family": "structure", "points": 60, "awarded": 60, "evidence": {"digit": "8", "count": 6}},
    {"id": "palindrome", "family": "structure", "points": 35, "awarded": 0, "evidence": {"mirror": "888888"}},
    {"id": "paired_digits", "family": "structure", "points": 30, "awarded": 0, "evidence": {"blocks": ["88", "88", "88"]}},
    {"id": "digit_diversity", "family": "simplicity", "points": 20, "awarded": 20, "evidence": {"digits": ["8"]}}
  ]
}
```

Sum of `awarded` must equal `score`. A weaker property remains discoverable by
filters even when a stronger match consumed that family's award. Break equal
rule awards within a family by a fixed rule-ID ordering.

## Finding the actual top 10,000 efficiently

### Exactness contract

The default build must find the true top 10,000 **under this scoring profile**
for each supported length. A small arbitrary sample or the old 250,000-generation
cap cannot establish that claim. Remove that cap from exact builds.

We can avoid enumerating every label if every positive-scoring rule has a
complete constructor. Let C be the union of those constructors' outputs:

1. Every label with a positive score must appear in C.
2. Labels outside C have score zero by definition of this profile.
3. If C contains at least 10,000 distinct positive-scoring labels, the top 10,000
   of C are also the top 10,000 of the full namespace, including tie-breaking.

This is a coverage obligation to test, not an assumption that “patterns look
representative.” A future scoring rule may only join exact mode when its
constructor or another coverage argument is available.

### Constructor coverage

| Positive rules covered | Constructor |
| --- | --- |
| Digit diversity; uniform; repeated-digit chunks | Enumerate labels over each digit subset of size 1–3; yield a label only for its exact distinct-digit set |
| Repeating blocks | Enumerate base blocks whose width divides N and repeat them to length N |
| Palindromes | Enumerate and mirror the first `ceil(N / 2)` digits |
| Paired digits; stepping pairs | Enumerate the N/2 collapsed digits and double each one, for even N |
| Whole/dominant sequences | Place every qualifying ascending/descending run at every possible position and enumerate the remaining free digits |
| Counting blocks | Enumerate starts for 2-/3-digit blocks, directions, and all lengths with at least three blocks; reject overflow |
| Near repetition | Mutate exactly one position of each allowed uniform/repeating seed |
| Roundness | Enumerate the short prefix and append a qualifying zero tail; require a nonzero prefix digit |
| Dates/constants | Enumerate the finite configured calendar ranges and curated constants |

For example, the <=3-distinct-digit constructor produces 67,600 unique six-digit
labels and 2,200,960 unique nine-digit labels. That alone guarantees enough
positive-scoring candidates for the default 10,000-per-length target. Other
constructors add high-scoring names outside those small digit alphabets.

This is larger than today's tiny pool, but far smaller than one billion
nine-digit labels. It requires real benchmarking; do not promise a subsecond
full build. Generator overlaps are expected, and no match may be missed because
one generator happened to run first.

### Selection and memory

- Process one length at a time, with a size-10,000 heap holding the best candidates.
- Order by **score descending, then label ascending** within each length. Labels
  remain zero-padded strings. Equal scores must not depend on generator order.
- Deduplicate labels currently retained in the heap. Rejected/evicted duplicates
  do not need a global set: with a deterministic score and a monotonically
  improving threshold, they cannot later beat the heap cutoff.
- Store compact heap records; generate full property explanations for final
  winners after selection, using the same scorer.
- Avoid keeping millions of scored objects or rejected rows in memory or SQLite.
- Record raw emissions/scoring work honestly; overlapping constructors mean raw
  emissions are not a unique-candidate count.
- Emit per-length progress, elapsed time, and the current cutoff to stderr.

An interrupted or explicitly budget-limited run must not replace an exact catalog
with partial results. An optional future approximate preview would need an
explicit label and separate metadata; it is not the default build.

## Calibration and performance before the full build

Start with a pure, easy-to-review scorer and a table of expected examples.
Keep weights in one small Python profile, not a plugin system or arbitrary code
configuration. Store its version and resolved constants/ranges with the build.

Benchmark a representative sample of scorer calls and constructor output,
including nine-digit labels, before estimating runtime. If profiling shows that
scoring rejected labels dominates, compute shared primitives once (digit counts,
runs, minimal period, symmetry), short-circuit impossible matches, and delay
explanation allocations until after selection. Near repetition must not compare
each label with thousands of seeds: for an eligible block width, group positions
by their offset within the block. The minimum substitutions needed are the label
length minus the sum of each group's most frequent digit count. A distance of
exactly one establishes this rule in a small amount of work per width.

Validate exact selection against an exhaustive oracle over all 1,000,000
six-digit labels. This is a bounded development verification task, not a return
to million-row persistent catalogs or million-request network scans. Compare
both the positive-scoring set coverage and the ordered top 10,000. Also use
smaller synthetic universes to exercise edge cases and ties cheaply.

For seven through nine digits, test coverage of each rule with independently
constructed examples, mutation cases, and sampled labels. The exactness argument
comes from complete rule constructors; sampling is supplementary evidence.
Use the built-in standard library first. Add multiprocessing only if measurements
show the full build is too slow; lengths are naturally independent work units.

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
on `(length, score DESC, label)` for ranked length views. Revisit a mixed-score
index only if actual all-length query performance warrants it.

Build metadata includes schema/scoring versions, resolved profile, default
lengths `[6,7,8,9]`, `keep_per_length=10000`, exactness/coverage method, build time,
and per-length row count, raw candidate work, elapsed time, and cutoff score.
A total of 40,000 rows must not conceal a missing length or an unbalanced split.

Publish a new database atomically only after all requested lengths finish and
validation passes. Use the current safe temporary-file workflow. Support the
current v1 schema as an observation source during replacement: preserve status,
provider, timestamps, and prices for retained domains. New rows stay unchecked.
Do not fabricate scores for old rows or reset existing registrar observations.
Existing replacement semantics still apply to candidates that drop out.

The SQLite file remains local and ignored by Git. Commit the scorer, constructors,
code, tests, and documentation; persist only winning domains as database rows.

## CLI changes

Proposed interface, to be implemented after this plan:

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

Keep `generate` for transient experimentation, but make its ordering use the same
scorer. A filtered or explicitly capped preview is described as such and must not
silently replace the exact default catalog.

## Website changes

The broader catalog needs more than adding three options to the existing selector:

- Show **All / 6 digits / 7 digits / 8 digits / 9 digits**, with actual counts.
- Show total saved domains and per-length coverage. Default completed state is
  40,000 total and 10,000 in each cohort.
- Add a score column; label rank as “Rank in length.” Do not imply a global rank
  when the all-length list contains four separate #1 entries.
- Default all-length ordering: score descending, then length ascending, then
  label ascending. Within a selected length use score descending, label ascending.
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

## Implementation sequence

1. **Scorer and examples:** implement pure property detection, integer family
   awards, evidence, and `score`; review representative examples from all lengths.
   Freeze the proposed v1 weights only after that review.
2. **Covered candidate search:** add complete rule constructors and heap selection;
   prove six-digit agreement against exhaustive scoring and benchmark larger pools.
3. **Catalog v2:** add score/properties, per-length ranks, metadata, and migration
   that preserves retained observations. Build and verify all four 10,000-row cohorts.
4. **CLI and website:** expose scores, explanations, length cohorts, and server-side
   filtered pagination/export. Update the README as a reader guide to implemented
   behavior, without treating planned commands as available today.
5. **Quality review:** inspect high/middle/cutoff examples for each length, compare
   property distributions, and confirm results feel worth browsing. If the weights
   change, bump the scoring version and rebuild deterministically. Specifically
   check whether recognizable constants survive the cutoff, incidental dates earn
   too much, or one structural property dominates a length. Adjust transparent
   weights rather than quietly adding family quotas to a score-sorted top 10,000.

Namecheap remains deferred throughout these steps. Better catalog coverage is
not authorization to check all 40,000 names online.

## Acceptance criteria

- Default database has exactly 10,000 unique rows for each supported length and
  40,000 total, with ranks 1–10,000 independently in each cohort.
- All stored scores equal the sum of awarded property points. Every matched
  property has reproducible evidence; shadowed matches do not double-award points.
- The same profile produces identical scores and ordered winners regardless of
  generator order. Leading zeros remain intact throughout.
- Exact six-digit winners match exhaustive scoring, including score ties.
  Every positive rule has a tested coverage constructor for all supported lengths.
- Weights, constants, date ranges, schema version, and per-length cutoffs are saved
  with the catalog. Completed catalogs cannot be silently approximate.
- Rebuild failure leaves the prior catalog usable; retained registrar observations
  survive migration. Rejected candidates are never written to the final database.
- Website counts show all four cohorts. Length/property/score filters, pagination,
  detail breakdowns, and exports agree with SQLite; initial page load is bounded
  by page size rather than total catalog size.
- Runtime and peak memory are measured before setting build-time expectations.
  No Namecheap request or credential persistence occurs during this work.

## Scope guardrails

No learned model, resale-price predictor, opaque fractional score, scoring plugin
framework, full namespace database, random candidate sampling presented as exact,
or automatic availability crawl. Add personal/cultural scoring only through
explicit preferences, and add new positive rules only with a coverage strategy.

Use the existing mise-selected Python 3.14, uv environment, and standard-library
SQLite/tooling. The original scanner remains preserved on `backup`.
