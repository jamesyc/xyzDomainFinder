"""Repeatedly check the highest-scoring unchecked rows; saved status is progress."""

import csv
import json
import math
import random
import signal
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager

import catalog
import namecheap


@contextmanager
def cancellation_signals(event):
    previous = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: event.set())
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run(database, *, max_checks=None, max_requests=None, timeout=None,
        stop_event=None, client_factory=None, progress=None):
    if stop_event is None:
        event = threading.Event()
        with cancellation_signals(event):
            return run(database, max_checks=max_checks, max_requests=max_requests, timeout=timeout,
                       stop_event=event, client_factory=client_factory, progress=progress)
    started = time.monotonic()
    deadline = started + timeout if timeout is not None else math.inf
    stats = {'total': 0, 'processed': 0, 'available': 0, 'unavailable': 0, 'unknown': 0,
             'live_names': 0, 'remaining': 0, 'requests': 0, 'current_score': None,
             'current_length': None, 'wait_seconds': 0, 'wait_reason': None, 'wait_until': None}
    client = None
    status, reason = 0, 'input_exhausted'
    writer = csv.DictWriter(sys.stdout, fieldnames=(*catalog.COLUMNS, 'cached', 'eligible'), extrasaction='ignore')
    selected = {}

    def notify(**updates):
        stats.update(updates)
        stats['requests'] = client.requests if client else 0
        stats['elapsed'] = round(time.monotonic() - started, 2)
        if progress:
            progress(dict(stats))
        else:
            print('SCAN_PROGRESS ' + json.dumps(stats), file=sys.stderr, flush=True)

    def stop_if_requested():
        if stop_event.is_set():
            raise namecheap.Cancelled()
        if time.monotonic() >= deadline:
            raise namecheap.BudgetEnded('deadline')

    def save(results):
        if not results:
            return
        catalog.save_observations(database, results)
        for result in results:
            writer.writerow({**selected[result['domain']], **result, 'cached': False,
                             'eligible': result['availability'] == 'available'})
            stats['processed'] += 1
            stats[result['availability']] += 1
        sys.stdout.flush()
        stats['remaining'] = stats['total'] - stats['processed']
        notify()

    with catalog.write_lock(database):
        catalog.ensure_observation_schema(database)
        stats['total'] = stats['remaining'] = catalog.unchecked_info(database)['total']
        writer.writeheader()
        sys.stdout.flush()
        notify()
        empty_batches = 0
        try:
            while True:
                stop_if_requested()
                capacity = min(50, max_checks - stats['live_names']) if max_checks is not None else 50
                if capacity <= 0:
                    reason = 'name_budget'
                    break
                rows = catalog.check_selection(database, {'state': 'unchecked'}, capacity)
                if not rows:
                    stats['remaining'] = 0
                    break
                selected = {row['domain']: row for row in rows}
                notify(current_score=rows[0]['score'], current_length=rows[0]['length'])
                if client is None:
                    client = (client_factory or namecheap.Client)(
                        max_requests if max_requests is not None else math.inf, deadline,
                        stop_event=stop_event, progress=notify)
                pending = list(selected)
                latest = {}
                counted, service_failed, conclusive = False, False, 0
                for attempt in range(3):
                    stop_if_requested()
                    before = client.requests
                    try:
                        result = client.check(pending)
                        latest = {domain: observation for domain, observation in result.items()
                                  if observation['availability'] == 'unknown'}
                        successes = [observation for observation in result.values() if observation['availability'] != 'unknown']
                        # A stop request during the HTTP call must not discard valid results.
                        save(successes)
                        conclusive += len(successes)
                        pending = list(latest)
                        service_failed = False
                        delay = 2**attempt + random.uniform(0, .25)
                    except namecheap.APIError as error:
                        if not error.retryable:
                            raise
                        latest = {domain: namecheap.unknown(domain, str(error)) for domain in pending}
                        service_failed = True
                        delay = error.delay if error.delay is not None else 2**attempt + random.uniform(0, .25)
                    finally:
                        if client.requests > before and not counted:
                            stats['live_names'] += len(rows)
                            counted = True
                    stop_if_requested()
                    if not pending:
                        break
                    if attempt == 2:
                        save(list(latest.values()))
                        pending = []
                        break
                    if client.requests >= client.max_requests:
                        raise namecheap.BudgetEnded('request_budget')
                    client.wait(delay, 'provider backoff' if service_failed else 'retry backoff')
                empty_batches = 0 if conclusive else empty_batches + 1
                if service_failed or empty_batches >= 3:
                    status, reason = 1, 'service_failures'
                    break
                notify()
        except namecheap.Cancelled:
            status, reason = 130, 'cancelled'
        except namecheap.BudgetEnded as error:
            reason = str(error)
        except namecheap.APIError as error:
            status, reason = 1, 'provider_error'
            print(f'Namecheap stopped: {error}. Unresolved names remain unchecked.', file=sys.stderr)
        except BrokenPipeError:
            reason = 'output_closed'
            raise
        except (OSError, sqlite3.Error, ValueError) as error:
            status, reason = 1, 'storage_error'
            print(f'Scan stopped while saving/accounting for work: {error}', file=sys.stderr)
        finally:
            notify(stop_reason=reason, wait_seconds=0, wait_reason=None, wait_until=None)
            print(f'Stop: {reason}; processed={stats["processed"]}; remaining={stats["remaining"]}; '
                  f'requests={client.requests if client else 0}', file=sys.stderr, flush=True)
    return status
