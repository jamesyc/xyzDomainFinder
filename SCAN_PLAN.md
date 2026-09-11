# Scan unchecked domains by score

## Goal

Let the website work through the existing catalog's unchecked domains, highest
score first. The user can cancel halfway through and later start again.

**The domain status is the checkpoint.** There is no separate resume operation,
scan-item queue, persistent cursor, or scan-history table. Every start uses the
same query against the current database.

This workflow is implemented. Installing or opening the website does not start a scan; Start is an explicit user action.

## Core loop

```sql
SELECT domain
FROM domains
WHERE availability = 'unchecked'
ORDER BY score DESC, length ASC, rank ASC
LIMIT 50;
```

1. Acquire the existing single-writer lock so another checker or rebuild cannot
   race this worker. Keep ordinary browsing available.
2. Check for cancellation and wait for a permitted Namecheap request slot.
3. Query the next batch using the statement above, always from the top.
4. Check the batch and commit the returned observations to `domains`.
5. Repeat until no unchecked names remain, the user cancels, or a blocking error
   requires attention.

Successful writes remove those rows from the next query automatically. There is
no SQL OFFSET and no numeric cursor to advance. Cancelling and starting again
naturally continues with whatever is still unchecked under the current scores.

## Strict eligibility

Only `availability = 'unchecked'` is eligible. Skip available, unavailable, and
unknown rows, regardless of age. Do not use the 15-minute cache TTL to bring old
results back into this scan.

A separate selected-name check can deliberately revisit a known or unknown name.
The existing small-check workflow keeps that ability. The bulk scan does not.

“All” means all retained names currently in this catalog, across all digit
lengths—not the full numeric namespace. Current table filters must not silently
change the scope of a button labeled **Scan all unchecked**.

## Saving results and cancelling

The website already persists actual Namecheap observations. The bulk worker uses
the same observation columns and transaction helpers.

- Commit each completed response before requesting another batch.
- Save valid entries from partial responses immediately. Retry unresolved entries
  within that active batch at most twice, subject to rate limits and cancellation.
- After retries are exhausted, save a final `unknown` outcome for those names.
  They then leave the unchecked scan, as requested.
- Cancellation stops new batches. An active request may finish; save valid results
  already received before stopping.
- If cancellation interrupts a request without a final result, leave unresolved
  rows unchecked. An aborted request is not a completed observation.
- Do not save temporary unknown outcomes on every intermediate retry. Keep that
  transient retry state in memory until success, terminal failure, or cancellation.

Use a short per-request deadline (initially 10 seconds) and interruptible rate
waits. Show **Stopping…** promptly after Cancel, then stop after the bounded
in-flight operation and any brief database commit. Cancellation must not abandon
an already received valid response merely to stop a fraction of a second sooner.

If the worker dies before committing a response, those rows remain unchecked and
may be requested again on the next Start. That limited duplicate work is acceptable
and simpler than trying to guarantee exactly-once external requests. Never mark
rows checked before a response has been saved.

## Website behavior

Keep the existing selected-name workflow and add one separate action:

1. **Scan all unchecked** opens a preview showing the current unchecked count,
   counts by length, example high-scoring names, request pacing, and estimated time.
2. **Start scan** launches one worker over the current unchecked set. It does not
   stop after finding ten available names; it keeps going until cancelled, blocked,
   or out of unchecked rows.
3. Show current-run counts (checked, available, unavailable, unknown), remaining
   unchecked rows, current score/length, requests, elapsed time, and any rate wait.
4. **Cancel scan** stops the worker and preserves all committed observations.
5. After stopping, offer **Start scan** again. It executes the identical query;
   no Resume button, recovery wizard, or reset operation is needed.

The running process/status can remain in the existing supervisor's memory.
Reloading the page reconnects to that active process. After the server restarts,
show the database's current unchecked count and an idle Start button. The user
starts explicitly; there is no automatic work after restart.

Closing the browser does not stop the worker while the local server is running.
Stopping the server normally cancels its owned worker. The server and computer
must stay running; an OS daemon or scheduled task is outside this feature.

Prevent duplicate workers across tabs and CLI usage with the existing writer
lock and start guards. A rebuild cannot publish over an active scan. Once the
scan stops, a rebuild is allowed; a later Start simply uses the rebuilt catalog's
current scores and unchecked statuses. No frozen scoring generation is required.

## Rate limits: the one piece of extra state worth keeping

Starting again must not reset the account's recent request usage. Reuse one durable
Namecheap request limiter for both selected checks and bulk checks. This is rate
accounting, not a domain resume mechanism.

Namecheap currently documents up to 50 names per check, with shared-key limits of
50 requests/minute, 700/hour, and 8,000/day. Reverify at implementation time.
Sources: [check API](https://www.namecheap.com/support/api/methods/domains/check/),
[API FAQ](https://www.namecheap.com/support/knowledgebase/article.aspx/9739/63/api-faq/).

Initial policy:

- One serial batch of at most 50 names.
- At least 1.5 seconds between request starts.
- App-wide ceilings at 80% of published limits: 40/minute, 560/hour, and 6,400/day.
- Persist request reservations/timestamps across starts and server restarts;
  count retries too and discard records outside the required rolling window.
- Share accounting across catalog files using the same configured account.
  A small local request ledger is enough; never store API keys in it.
- Honor `Retry-After`. Keep long waits cancellable and display the next permitted
  request time. Do not retry credential/allowlist failures against the whole catalog.

Requests from unrelated software using the key are invisible to this app, so
provider throttling remains authoritative. Repeated service failures should stop
with an actionable message, leaving unattempted rows unchecked.

For scale, 40,000 unchecked names require roughly 800 full batches. With unused
quotas, minute-paced requests followed by the required hourly wait give a planning
baseline of about 66 minutes. Latency, retries, and existing usage add time; do not
promise that timing. A persistent rolling limiter, rather than a fixed delay alone,
enforces all three windows. The UI shows elapsed time and the current quota wait.

## Implementation shape

- Reuse the current Namecheap client, strict parsing, observation storage, writer
  lock, and supervised child process.
- Add a dedicated `scan` loop that repeatedly selects the next unchecked batch.
  Do not repeatedly call the short `run()` with newly reset budgets or TTL selection.
- Keep batch retry state in memory. The `domains` table records completed outcomes;
  no domain-progress schema migration is needed.
- Extend the supervisor/status API for the longer operation and cooperative stop.
- Add bulk preview/start/cancel endpoints with the existing local Host/Origin,
  JSON validation, and replay/duplicate-start protections. Do not send thousands
  of domain names in browser payloads; the server queries SQLite.
- Maintain compact in-memory progress and bounded diagnostics. After restart,
  derive remaining work from SQLite rather than recovering an old job record.

## Validation

1. Available, unavailable, and unknown rows are never selected, even when stale.
2. Each new batch starts at the highest-scoring remaining unchecked row.
3. Cancelling after several batches preserves results; Start again visits only
   remaining unchecked rows without skips caused by OFFSET or expired cache TTLs.
4. Cancel during rate waits and network calls stays responsive. Valid completed
   results are committed; unresolved cancelled work remains unchecked.
5. Crash before a commit causes at most a repeated batch, not a lost result or
   falsely advanced status. Transaction failures leave the batch consistent.
6. Partial responses save successes and retry unresolved names only. Terminal
   unknown outcomes are excluded from subsequent unchecked queries.
7. Rate history survives restarting and includes manual checks/retries. Authentication
   failures stop without consuming the remaining catalog.
8. Two tabs cannot launch duplicate workers. Browsing works during the scan;
   builds and other checkers cannot overwrite active writes.
9. A small mocked integration scan verifies the full Start → Cancel → Start flow.
   A bounded live check can follow; do not launch the entire catalog during setup.

## Implementation notes

- `scan.py` repeatedly queries unchecked rows without OFFSET or a resume cursor.
  It saves successes immediately, keeps unresolved retries in memory, and leaves
  unanswered cancellation work unchecked. A partial index makes the next-batch
  query efficient as known results accumulate.
- `rate_limit.py` reserves request slots atomically in an app-level SQLite ledger
  shared by both checking modes and all catalogs for the configured account. It
  persists provider cooldowns and applies 40/minute, 560/hour, and 6,400/day.
- The website adds a separate full-scan preview/start action and reuses the
  supervisor's cancel control. Progress stays bounded in memory; result rows
  remain the only domain checkpoint. No background scan starts automatically.
- Unit tests cover Stop → Start ordering, terminal unknown exclusion, partial
  responses, failed saves, real process cancellation, quota windows, concurrent
  reservations, and cooldown persistence.
- The scan-preview regression was an HTML 404 from an old running backend. The
  backend now returns JSON API errors and the shared browser API helper handles
  non-JSON failures explicitly. JavaScript tests reproduce that exact failure,
  plus malformed success bodies, aborts, and structured errors.

Run `uv run python -m unittest` and `node --test test_web_api.mjs` for the regression
suites. The real catalog is not used for synthetic scan tests.
