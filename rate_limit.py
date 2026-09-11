"""Durable Namecheap request accounting shared across this user's catalogs."""

import hashlib
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

# 80% of Namecheap's published 50/minute, 700/hour, 8,000/day limits.
LIMITS = {'minute': 40, 'hour': 560, 'day': 6400}
INTERVAL = 60 / LIMITS['minute']
WINDOWS = ((60, LIMITS['minute'], 'minute quota'), (3600, LIMITS['hour'], 'hour quota'),
           (86400, LIMITS['day'], 'day quota'))


def estimate_seconds(requests):
    """Planning baseline with an empty ledger; latency and prior usage add time."""
    slots = max(0, requests - 1)
    return max([slots * INTERVAL] + [(slots // limit) * window + (slots % limit) * INTERVAL
                                    for window, limit, _ in WINDOWS])


def default_path():
    base = (Path.home() / 'Library' / 'Application Support' if sys.platform == 'darwin'
            else Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local' / 'state')))
    return base / 'xyzDomainFinder' / 'namecheap-requests.sqlite3'


class Ledger:
    def __init__(self, account, path=None, clock=None):
        self.path = Path(path) if path else default_path()
        self.account = hashlib.sha256(account.strip().lower().encode()).hexdigest()
        self.clock = clock or time.time
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        except FileExistsError:
            pass
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS requests (account TEXT NOT NULL, at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS requests_account_time ON requests(account, at);
                CREATE TABLE IF NOT EXISTS cooldowns (account TEXT PRIMARY KEY, until REAL NOT NULL);
            ''')

    def reserve(self):
        """Return (wait seconds, reason), reserving a slot only when allowed now."""
        now = self.clock()
        with closing(sqlite3.connect(self.path, timeout=5)) as connection:
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('DELETE FROM requests WHERE at <= ?', (now - 86400,))
                times = [row[0] for row in connection.execute(
                    'SELECT at FROM requests WHERE account=? ORDER BY at', (self.account,))]
                delay, reason = 0.0, None
                if times and times[-1] + INTERVAL > now:
                    delay, reason = times[-1] + INTERVAL - now, 'request spacing'
                for window, limit, label in WINDOWS:
                    recent = [stamp for stamp in times if stamp > now - window]
                    if len(recent) >= limit:
                        wait = recent[-limit] + window - now
                        if wait > delay:
                            delay, reason = wait, label
                cooldown = connection.execute('SELECT until FROM cooldowns WHERE account=?', (self.account,)).fetchone()
                if cooldown and cooldown[0] - now > delay:
                    delay, reason = cooldown[0] - now, 'provider backoff'
                if delay > 0:
                    return delay, reason
                connection.execute('INSERT INTO requests VALUES (?,?)', (self.account, now))
                return 0.0, None

    def defer(self, seconds):
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute('''INSERT INTO cooldowns VALUES (?,?)
                    ON CONFLICT(account) DO UPDATE SET until=MAX(until,excluded.until)''',
                    (self.account, self.clock() + seconds))
