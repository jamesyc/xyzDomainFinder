"""One bounded CLI check at a time, supervised by the local website."""

import csv
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import catalog
import namecheap
import rate_limit


class Conflict(ValueError):
    pass


class CheckManager:
    def __init__(self, database):
        self.database = Path(database).resolve()
        self.lock = threading.Lock()
        self.plan = None
        self.job = None
        self.process = None
        self.worker = None

    def preview(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'domains', 'refresh'}:
            raise ValueError('Expected selected domains and an optional refresh flag')
        domains = payload.get('domains')
        refresh = payload.get('refresh', False)
        if not isinstance(domains, list) or not 1 <= len(domains) <= 50:
            raise ValueError('Select between 1 and 50 names')
        if type(refresh) is not bool:
            raise ValueError('Refresh must be true or false')
        if any(not isinstance(domain, str) or not re.fullmatch(r'[0-9]{6,9}\.xyz', domain) for domain in domains):
            raise ValueError('Only six- through nine-digit .xyz names are supported')
        domains = list(dict.fromkeys(domains))
        rows = catalog.check_selection(self.database, {}, len(domains), domains)
        if len(rows) != len(domains):
            raise ValueError('Some selected names are no longer in the catalog')
        configured = all(os.getenv(key, '').strip() for key in
                         ('NAMECHEAP_USERNAME', 'NAMECHEAP_API_KEY', 'NAMECHEAP_CLIENT_IP'))
        plan = {'id': uuid.uuid4().hex, 'mode': 'selected', 'domains': domains, 'refresh': refresh,
                'target': len(domains), 'max_checks': len(domains), 'max_requests': 20,
                'timeout': 60, 'cached': sum(not refresh and namecheap.fresh(row, 15) for row in rows),
                'configured': configured,
                'names': [{'domain': row['domain'], 'score': row['score'], 'availability': row['availability']} for row in rows]}
        with self.lock:
            self.plan = (plan, time.monotonic() + 300)
        return plan

    def preview_scan(self, payload):
        if not isinstance(payload, dict) or payload:
            raise ValueError('The unchecked scan does not accept a filtered domain list')
        info = catalog.unchecked_info(self.database)
        plan = {'id': uuid.uuid4().hex, 'mode': 'scan', 'selected': info['total'],
                'by_length': info['by_length'], 'names': info['examples'],
                'estimated_seconds': rate_limit.estimate_seconds((info['total'] + 49) // 50),
                'request_limits': dict(rate_limit.LIMITS),
                'configured': all(os.getenv(key, '').strip() for key in
                                 ('NAMECHEAP_USERNAME', 'NAMECHEAP_API_KEY', 'NAMECHEAP_CLIENT_IP'))}
        with self.lock:
            self.plan = (plan, time.monotonic() + 300)
        return plan

    def start(self, plan_id, expected_mode='selected'):
        with self.lock:
            if self.job and self.job['state'] in ('running', 'cancelling'):
                raise Conflict('A check is already running')
            if not self.plan or self.plan[0]['id'] != plan_id or time.monotonic() >= self.plan[1]:
                raise Conflict('This preview expired. Review the selection again.')
            plan = self.plan[0]
            if plan['mode'] != expected_mode:
                raise Conflict('Review the correct check mode before starting')
            if plan['mode'] == 'scan':
                selected = catalog.unchecked_info(self.database)['total']
                if not selected:
                    raise Conflict('There are no unchecked names left')
                command = [sys.executable, '-u', str(Path(__file__).with_name('xyz.py')), 'scan',
                           '--database', str(self.database)]
            else:
                selected = len(plan['domains'])
                catalog.check_selection(self.database, {}, selected, plan['domains'])
                command = [sys.executable, '-u', str(Path(__file__).with_name('xyz.py')), 'check',
                       '--database', str(self.database), '--limit', str(len(plan['domains'])),
                       '--min-score', '0',
                       '--max-checks', str(plan['max_checks']), '--max-requests', str(plan['max_requests']),
                       '--timeout', str(plan['timeout']), '--target', str(plan['target'])]
                for domain in plan['domains']:
                    command.extend(['--number', domain])
                if plan['refresh']:
                    command.append('--refresh')
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding='utf-8', cwd=Path(__file__).parent,
                                       start_new_session=True)
            self.process = process
            self.job = {'id': uuid.uuid4().hex, 'mode': plan['mode'], 'started_at': time.time(), 'state': 'running', 'selected': selected,
                        'completed': 0, 'available': 0, 'unavailable': 0, 'unknown': 0, 'cached': 0,
                        'exit_code': None, 'diagnostic': '', 'results': [], 'progress': {}, 'cancel_requested': False}
            self.plan = None
            job_id = self.job['id']
            self.worker = threading.Thread(target=self._watch, args=(process, job_id), daemon=True)
            self.worker.start()
            return self._snapshot()

    def _snapshot(self):
        return {**self.job, 'results': list(self.job['results']), 'progress': dict(self.job['progress'])} if self.job else None

    def status(self):
        with self.lock:
            return self._snapshot()

    def _watch(self, process, job_id):
        def read_errors():
            for line in process.stderr:
                key = os.getenv('NAMECHEAP_API_KEY')
                if key:
                    line = line.replace(key, '[redacted]')
                with self.lock:
                    if self.job['id'] == job_id:
                        if line.startswith('SCAN_PROGRESS '):
                            try:
                                progress = json.loads(line[len('SCAN_PROGRESS '):])
                                self.job['progress'] = progress
                                self.job['selected'] = progress['total']
                            except (ValueError, KeyError):
                                pass
                        else:
                            self.job['diagnostic'] = (self.job['diagnostic'] + line)[-5000:]

        errors = threading.Thread(target=read_errors, daemon=True)
        errors.start()
        try:
            for row in csv.DictReader(process.stdout):
                state = row.get('availability')
                if state not in ('available', 'unavailable', 'unknown'):
                    continue
                with self.lock:
                    self.job['completed'] += 1
                    self.job[state] += 1
                    self.job['cached'] += row.get('cached') == 'True'
                    self.job['results'].append({'domain': row['domain'], 'availability': state,
                                                'cached': row.get('cached') == 'True'})
                    del self.job['results'][:-50]
        finally:
            code = process.wait()
            errors.join()
            process.stdout.close()
            process.stderr.close()
            with self.lock:
                self.job['exit_code'] = code
                self.job['state'] = ('completed' if code == 0 else
                                     'cancelled' if self.job['cancel_requested'] and code in (130, -signal.SIGINT) else 'failed')

    def cancel(self, job_id):
        with self.lock:
            if not self.job or self.job['id'] != job_id:
                raise Conflict('That check is no longer active')
            if self.job['state'] == 'running':
                self.job['cancel_requested'] = True
                self.job['state'] = 'cancelling'
                self.process.send_signal(signal.SIGINT)
            return self._snapshot()

    def close(self):
        with self.lock:
            active = self.job and self.job['state'] in ('running', 'cancelling')
            process = self.process
            if active:
                self.job['cancel_requested'] = True
                process.send_signal(signal.SIGINT)
        if active:
            try:
                process.wait(timeout=12 if self.job['mode'] == 'scan' else 5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if self.worker:
            self.worker.join(timeout=5)
