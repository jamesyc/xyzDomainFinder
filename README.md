# xyzDomainFinder

Find interesting numeric `.xyz` domains by scoring them locally, keeping a useful
collection in SQLite, and exploring it before making any registrar queries.

## Build and explore

The project uses **Python 3.14 through mise**, with **uv** managing `.venv` and the
lockfile. No third-party Python dependencies are required.

```sh
mise install
mise exec -- uv sync
uv run xyz.py build --replace
uv run xyz.py serve
```

Open [the local catalog](http://127.0.0.1:8765). Switch between six-, seven-, eight-,
and nine-digit collections; search digits; filter by interesting property or
minimum score; open a name to see every contributing rule. Export downloads all
matching results, not just the current page. **Refresh data** rereads SQLite.

The viewer binds to localhost and is read-only. It makes no registrar requests.
Use `--database PATH` with `build`, `find`, or `serve` for another catalog, and
`--port PORT` with `serve` for another port. If mise is not active in your shell,
prefix `uv run` commands with `mise exec --`.

## What makes a number interesting?

Each name gets integer points across five families. Only the strongest matching
rule in each family contributes; independent family awards add together.

| Family | Properties and points |
| --- | --- |
| Structure | Uniform digits 60; repeating blocks 45; palindromes 35; pairs 30; repeated-digit chunks or near repetition 20 |
| Progression | Whole sequences 45; counting blocks 35; stepping pairs 30; dominant consecutive runs earn a length-scaled bonus |
| Simplicity | One distinct digit 20; two digits 14; three digits 6 |
| Roundness | A sufficiently long zero ending 25 |
| Meaning | Recognized mathematical constants 55; a valid date in the configured formats/ranges 12 |

For example, `888888.xyz` gets **80** points: uniform digits (60) and one distinct
digit (20). Its palindrome and paired-digit matches remain visible, but add no
extra structure points. `121212.xyz` gets **71**: repetition (45), two digits (14),
and the date 2012-12-12 (12).

Dates use YYMMDD in 2000–2099 or YYYYMMDD in 1900–2099. No date meaning is inferred
for seven or nine digits. Constants are pi, e, and the golden ratio, using the
first N digits with the decimal point removed. Exact definitions, weights, and
source digits are versioned and saved in the catalog's scoring profile.

Scores express a preference for recognizable structure and meaning. They are
not resale values, prices, or availability predictions. The site shows matched
properties even when a stronger rule covers their points.

```sh
uv run xyz.py score 888888 121212 123456789
uv run xyz.py find --length 8 --min-score 50 --pattern palindrome --limit 20
uv run xyz.py generate --length 9 --pattern constant --limit 10 --format json
```

## A useful collection, not an exhaustive crawl

A default build considers all four supported lengths and retains up to **10,000
names per length**. That is a configurable browsing/checking budget, not a quota
to fill with arbitrary names or a claim that the entire namespace was searched.
A name's rank is within its own digit length. All-length views sort by score,
then length, then label, so ties remain deterministic.

Candidates come from direct pattern construction: repeats, symmetry, sequences,
near repeats, zero endings, dates, constants, and compact-digit fallbacks when
needed. The scorer evaluates the whole label regardless of its source. A bounded
heap keeps the best observed names without storing rejected candidates. The
build reports candidate work, retained counts, and cutoff scores per length.

```sh
# A wider collection, or a stricter minimum score.
uv run xyz.py build --keep-per-length 20000 --database wider.sqlite3
uv run xyz.py build --length 7 --length 9 --min-score 50 --database focused.sqlite3

# A deliberately smaller candidate-work budget for an exploratory build.
uv run xyz.py build --max-generated 20000 --database quick.sqlite3

# Export a filtered selection.
uv run xyz.py find --length 8 --min-score 60 --format text > shortlist.txt
```

`--max-generated` is optional and applies per length; capped builds are marked in
metadata and the viewer. A filtered/capped collection can contain fewer names.
`--keep-per-length` replaces the old ambiguous `--keep` option. Use `--help` for
pattern filters, explicit numbers, input files, date-generation ranges, and
output formats. Explicit numbers get the same score rules; `--min-score 0` also
permits supplied numbers with no recognized scoring property.

## Storage and rebuilds

`domains.sqlite3` stores only retained names, with score, rank-in-length, property
explanations, and nullable registrar observations. Build metadata records the
scoring version/profile, selection settings, and per-length counts and cutoffs.
The database and exports stay local and are ignored by Git.

A build does not overwrite a different selection without `--replace`. Replacement
publishes a completed SQLite file atomically, preserving recorded observations
for names that remain and dropping names outside the new selection. It also
migrates the earlier unscored catalog. Use a separate database path to retain
multiple collections. Interrupted builds leave the previous catalog intact.

## Why numeric .xyz domains?

The registry's **1.111B Class** covers all six-, seven-, eight-, and nine-digit
numeric labels, from `000000.xyz` through `999999999.xyz`. Leading zeros are part
of domain identity: `001234.xyz` differs from `1234.xyz`, which is outside this
class. Labels remain strings throughout generation, storage, and exports.

The class was introduced with advertised pricing of **US$0.99 per year**, making
numeric names attractive for experiments, dates, identifiers, and campaigns.
That historical price is context, not a quote: check the purchase registrar's
actual registration and renewal prices, currency, and fees.

See the [registry's numeric browser](https://gen.xyz/number),
[pricing page](https://gen.xyz/pricing), and
[2017 announcement](https://news.gandi.net/en/2017/06/introducing-the-1-111b-class-of-xyz-domains/).

## Availability is a separate observation

New names are **unchecked**. An explicit registrar response can establish
**available** or **unavailable** at a recorded time; failed or inconclusive checks
are **unknown**. Empty price fields mean unquoted, not zero. Recheck availability
and renewal terms at checkout; discovery never reserves or purchases a name.

DNS absence and missing RDAP records/status fields do not prove a name can be
registered. Registrar verification provides the practical purchase answer.
Namecheap verification is an explicit CLI action; catalog construction and the
viewer do not call it. See [Namecheap's check API](https://www.namecheap.com/support/api/methods/domains/check/)
and the [RDAP specification](https://www.rfc-editor.org/rfc/rfc9083.html).

## Check a selected shortlist with Namecheap

Configure `NAMECHEAP_USERNAME`, `NAMECHEAP_API_KEY`, and `NAMECHEAP_CLIENT_IP` in
your environment or the ignored `mise.local.toml` `[env]` section. Use
`redact = true` for the API key in mise and restrict that file to your user.
The IP must be your public IPv4 allowlisted in Namecheap's
[API settings](https://www.namecheap.com/support/api/intro/). Credentials are read
only when a live request is needed; they are not stored in SQLite or logs.
Live checks currently use macOS/Linux deadline controls.

```sh
# Review the exact catalog selection without credentials, writes, or requests.
uv run xyz.py check --length 8 --pattern constant --limit 20 --preview

# Check that selection, saving each completed response to SQLite.
uv run xyz.py check --length 8 --pattern constant --limit 20

# Check explicit catalog names or a plaintext/CSV shortlist exported from the site.
uv run xyz.py check --number 31415926.xyz --number 314159265.xyz
uv run xyz.py check --input shortlist.csv --max-checks 30 --target 5
```

Names must already exist in the catalog. Explicit inputs are validated before
requests and are still narrowed by any supplied filters. Defaults select at most
200 candidates, submit at most 50 distinct names, make at most 20 HTTP attempts,
and stop after 60 seconds or 10 eligible available results. Every retry consumes
the request budget. Completed observations survive interruption; a rebuild cannot
replace the database while checks are in progress.

Successful Namecheap observations are reused for 15 minutes by default, before
spending new requests. `--cache-minutes` changes that window; `--refresh` forces
new checks. Unknown results are retried rather than treated as cached success.
Refresh the website after checking to see states, times, price classes, and any
check errors. Its detail view uses a 15-minute reference window for observation
age; that label does not guarantee continued availability.

Premium status is displayed, not automatically rejected: Namecheap can mark the
inexpensive numeric class as premium. Use `--exclude-premium` if you want only
confirmed non-premium names to count toward the result target. Availability
remains separate from this eligibility filter.

The check API can supply premium price amounts without currency, term, or complete
fee context. These are saved as reported, with an explicit note; ordinary
per-domain prices may remain unquoted. Optional `--max-registration` and
`--max-renewal` ceilings require `--currency` and a confirmed one-year quote in
that currency. Unknown quote context cannot pass a price ceiling, even when a
raw amount looks inexpensive. Ceilings compare base prices, excluding fees/taxes.
Verify the final purchase and renewal terms at checkout.

Output is CSV with cached/eligible flags; budgets and stop reasons go to stderr.
Running out of candidates or budget is a normal partial result. Authentication
and configuration failures stop promptly; throttling/transient errors have at
most two retries and respect `Retry-After`. The tool never registers a domain.

## Earlier scanner

The [backup branch](https://github.com/jamesyc/xyzDomainFinder/tree/backup)
preserves the original exhaustive six-/seven-digit scanner, its README,
`available_domains.csv`, and `resume_state.json`. It used CentralNic RDAP or
DNS-over-HTTPS with throttling, retries, and saved progress. Those results are
historical observations, not a current availability list.
