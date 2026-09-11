# xyzDomainFinder

Find interesting numeric `.xyz` domains that you could actually register.

The useful result is a short list of memorable, meaningful, affordable names.
xyzDomainFinder starts with your preferences, generates matching numbers locally,
and saves only the best-ranked candidates in SQLite. Browse that catalog and
export a shortlist before checking names at your registrar.

## Why numeric .xyz domains?

The `.xyz` registry's **1.111B Class** covers every six-, seven-, eight-, and
nine-digit numeric combination, from `000000.xyz` through `999999999.xyz`.
Leading zeros are part of the name: `001234.xyz` and `1234.xyz` are different
domains, and the latter is outside this class.

The class was introduced with advertised pricing of **US$0.99 per year**, making
numeric names attractive for experiments, personal projects, memorable dates,
numeric identifiers, and campaigns. That historical price is context, not a
quote: check the registrar's actual registration and renewal prices, currency,
fees, and any special pricing before choosing a name.

See the registry's [numeric-domain browser](https://gen.xyz/number),
[pricing page](https://gen.xyz/pricing), and the
[2017 announcement](https://news.gandi.net/en/2017/06/introducing-the-1-111b-class-of-xyz-domains/).
For an occasional purchase, the registry browser and a registrar's bulk search
may be all you need. This project's value is in expressing your own preferences
and producing a useful shortlist repeatedly.

## From an idea to a shortlist

1. **Describe what interests you.** Choose lengths and patterns, supply meaningful
   numbers, or narrow the search with a prefix or suffix.
2. **Generate and rank locally.** Repeated blocks, palindromes, sequences, paired
   digits, round numbers, memorable chunks, and explicitly selected dates provide
   a manageable candidate pool. The shortlist mixes selected pattern families,
   and each result includes the reason it matches. Ranking expresses your
   preferences; it is not an appraisal of resale value.
3. **Keep and review the best candidates.** A build retains up to 1,000 names by
   default, with their ranks and match reasons in a local SQLite database.
   Search that catalog or export names for a registrar's bulk search. Building
   and browsing need no account, API credentials, or network access.
4. **Verify the names you like.** Check registration availability and prices
   through the registrar where you intend to purchase. Automated checks should
   have explicit limits on names, requests, and elapsed time.
5. **Choose and register at the registrar.** Recheck the selected name and its
   renewal terms before checkout. Discovery does not reserve or purchase a name.

For example, `123123.xyz` repeats a block, `123321.xyz` is a palindrome, and
`20260911.xyz` encodes a date. These illustrate patterns, not availability.

## Build and browse

```sh
mise install
mise exec -- uv sync
uv run xyz.py build
uv run xyz.py find --limit 20
uv run xyz.py find --pattern palindrome --contains 88
uv run xyz.py find --prefix 12 --format text > shortlist.txt
```

The default build selects six-digit names and stores at most 1,000 in
`domains.sqlite3`. Use repeated `--length` and `--pattern` options to choose
six- through nine-digit names and pattern families. `--keep` sets the number
retained; `--max-generated` bounds local construction work (250,000 constructions
by default). A cap warning means the pool may be incomplete; this is also recorded
in catalog metadata. Generated data stays local and is ignored by Git.

```sh
uv run xyz.py build --length 8 --length 6 --pattern repeat --pattern palindrome --keep 500 --replace
uv run xyz.py generate --length 9 --pattern palindrome --limit 20 --format csv
```

`--replace` intentionally replaces the catalog's candidate selection, preserving
recorded observations for names that remain. Use `--database PATH` to maintain a
separate catalog instead. Repeating the same build settings reuses the existing
catalog. The `generate` command prints candidates without changing SQLite.

Ranking gives explicit numbers first priority, then takes turns across pattern
families. Within each family it favors requested length order, fewer distinct
digits, and lexical order. The top-K cutoff is a preference, not a resale-value
estimate. Arbitrary unrecognized numbers do not fill unused capacity.

`find` searches only retained names; filters cannot recover names excluded from
the build. CSV shows the stored rank and reasons alongside observation fields.
`--format text` produces one domain per line. Use `--help` on each command for
filters, explicit number inputs, date ranges, and limits. If mise is not active
in your shell, prefix `uv run` commands with `mise exec --`.

## What an availability result means

A generated candidate is **unchecked**. A registrar can report it as available
or unavailable at a particular time. Failed requests, throttling, and incomplete
responses mean **unknown**. An unknown renewal price is not zero.

DNS and registration are different systems. A registered domain can have no DNS
delegation, so `NXDOMAIN` does not prove that it can be registered. RDAP supplies
registration data; a missing record or status field is not a registrar's offer
to sell a name. Registrar verification supplies the practical answer, subject to
changes before checkout. See [ICANN's DNS rules](https://itp.cdn.icann.org/en/files/registry-agreements/net/net-agmt-html-01jul17-en.htm)
and the [RDAP response specification](https://www.rfc-editor.org/rfc/rfc9083.html).

Saved checks need a source and timestamp. Their purpose is to avoid repeating
recent work; they cannot guarantee future availability. Network checks must
respect provider rate limits and `Retry-After`, retain completed work across
interruptions, and keep errors distinct from genuine negative results.

## Python tooling

The project targets **Python 3.14**, with **mise** selecting the interpreter and
providing **uv**. uv owns project dependencies, the lockfile, and the `.venv`
environment. Project commands run through `uv run` with the mise-selected Python.
See [mise's Python and uv integration](https://mise.jdx.dev/lang/python.html#mise-uv)
and [uv's project documentation](https://docs.astral.sh/uv/guides/projects/).

## Earlier scanner

The [backup branch](https://github.com/jamesyc/xyzDomainFinder/tree/backup)
preserves the original exhaustive six- and seven-digit scanner, its README,
`available_domains.csv`, and `resume_state.json`. It used CentralNic RDAP or
DNS-over-HTTPS, with retries, throttling controls, and saved progress.

Those results are historical observations from the old lookup rules. They are
not a current availability list or a restriction on which names to consider.
The new approach measures useful choices found for the effort spent checking
them, rather than coverage of the entire numeric namespace.
