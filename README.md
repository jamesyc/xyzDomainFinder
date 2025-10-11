# xyzDomainFinder

## Why 6+ digit numeric .xyz domains are special

The .xyz registry created the “1.111B Class” of domains covering every 6-,
7-, 8-, and 9-digit numeric combination from `000000.xyz` through
`999999999.xyz`. Members of this class are permanently priced at
$0.99 USD for the first year, making long numeric .xyz names an inexpensive
way to claim memorable numbers like phone numbers, dates, or ZIP codes for
experiments and campaigns.

## Finding available numeric .xyz domains

Use the asynchronous scanner in `scripts/xyz_domain_checker.py` to test each
6- or 7-digit numeric combination via the CentralNic RDAP service (or an
optional DNS-over-HTTPS mode). Results
are appended to `available_domains.csv`, and progress is persisted in
`resume_state.json` so a scan can be restarted without losing work. The
current dataset focuses on 6-digit names (`000000.xyz` through `999999.xyz`).

### Quick start

1. Install dependencies (requires Python 3.12+):
   ```bash
   pip install -r requirements.txt
   ```
   If you do not have a `requirements.txt`, install `aiohttp` manually.
2. Run a fresh 6-digit scan. CentralNic currently enforces long (5 minute)
   back-off windows when more than a handful of RDAP lookups are made in a
   short period, so start with a conservative rate cap that keeps traffic below
   50 queries per hour:
   ```bash
   python scripts/xyz_domain_checker.py --restart --length 6 --rate-limit 0.01
   ```
   At that speed a full pass takes days, but the run is resumable and prevents
   repeated 429/Retry-After responses. If you have direct permission from the
   registry or are running from an allow-listed network you can raise the rate.
3. Resume after an interruption:
   ```bash
   python scripts/xyz_domain_checker.py
   ```

### Useful options

- `--max-count N` – limit the total number of domains processed (helpful for
  quick tests).
- `--length 6` / `--length 7` – restrict the scan to one digit length.
- `--batch-size` / `--concurrency` – tune request throughput.
- `--resume-file` / `--output` – change where checkpoints and CSV results are
  written.
- `--rate-limit` – cap requests per second to avoid RDAP throttling (helpful
  when running unattended).
- `--lookup-mode {rdap,doh}` – choose between RDAP (default) and the
  Cloudflare DNS-over-HTTPS resolver for availability checks. The DoH mode is
  useful for quicker local experiments when RDAP throttling is too strict.
- `--doh-endpoint URL` – override the DNS-over-HTTPS endpoint used when in DoH
  mode (defaults to Cloudflare; compatible with providers like dns.google).

Every run appends available domains to the CSV in the format:

```text
domain,length
000001.xyz,6
```

The script retries transient errors, respects proxy settings via
`trust_env=True`, honours RDAP `Retry-After` headers, and saves progress
regularly so partial results are not lost during long scans.
