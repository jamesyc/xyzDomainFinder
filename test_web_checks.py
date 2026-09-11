import io
import json
import os
import signal
import subprocess
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

import catalog
import namecheap
import scoring
import viewer
import web_checks


class FakeProcess:
    def __init__(self, blocked=False, diagnostic='Stop: input_exhausted\n'):
        self.stdout = io.StringIO('domain,availability,cached\n888888.xyz,available,False\n')
        self.stderr = io.StringIO(diagnostic)
        self.done = threading.Event()
        self.returncode = None
        self.signals = []
        if not blocked:
            self.done.set()

    def wait(self, timeout=None):
        if not self.done.wait(timeout or 3):
            raise subprocess.TimeoutExpired('fake checker', timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def send_signal(self, value):
        self.signals.append(value)
        self.returncode = 130
        self.done.set()

    def kill(self):
        self.returncode = -9
        self.done.set()


class WebChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'catalog.sqlite3'
        row = scoring.score('888888', True)
        row['rank'] = 1
        catalog.build(self.path, [row], {}, {'6': {'examined': 1, 'retained': 1, 'capped': False}})
        self.manager = web_checks.CheckManager(self.path)
        self.addCleanup(self.manager.close)

    def test_preview_is_exact_and_does_not_write_or_spawn(self):
        before = self.path.read_bytes()
        with patch('web_checks.subprocess.Popen') as process:
            plan = self.manager.preview({'domains': ['888888.xyz', '888888.xyz']})
        process.assert_not_called()
        self.assertEqual(plan['domains'], ['888888.xyz'])
        self.assertEqual(plan['max_checks'], 1)
        self.assertEqual(plan['target'], 1)
        self.assertEqual(self.path.read_bytes(), before)
        for payload in ({'domains': []}, {'domains': ['888888.xyz'] * 51},
                        {'domains': ['888888.xyz;echo bad']}, {'domains': ['999999.xyz']},
                        {'domains': ['888888.xyz'], 'refresh': 'yes'}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.manager.preview(payload)

    def test_start_uses_frozen_preview_and_collects_safe_progress(self):
        plan = self.manager.preview({'domains': ['888888.xyz'], 'refresh': True})
        process = FakeProcess(diagnostic='test-api-key\nStop: input_exhausted\n')
        with patch.dict(os.environ, {'NAMECHEAP_API_KEY': 'test-api-key'}), \
             patch('web_checks.subprocess.Popen', return_value=process) as popen:
            job = self.manager.start(plan['id'])
            self.manager.worker.join(timeout=2)
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index('--number') + 1], '888888.xyz')
        self.assertEqual(command[command.index('--max-checks') + 1], '1')
        self.assertEqual(command[command.index('--min-score') + 1], '0')
        self.assertIn('--refresh', command)
        self.assertNotIn('shell', popen.call_args.kwargs)
        state = self.manager.status()
        self.assertEqual((state['state'], state['completed'], state['available']), ('completed', 1, 1))
        self.assertNotIn('test-api-key', state['diagnostic'])
        self.assertIn('[redacted]', state['diagnostic'])
        self.assertEqual(state['id'], job['id'])
        with self.assertRaises(web_checks.Conflict):
            self.manager.start(plan['id'])

    def test_cancel_and_concurrent_start(self):
        plan = self.manager.preview({'domains': ['888888.xyz']})
        process = FakeProcess(blocked=True)
        with patch('web_checks.subprocess.Popen', return_value=process):
            job = self.manager.start(plan['id'])
            with self.assertRaises(web_checks.Conflict):
                self.manager.start(plan['id'])
            state = self.manager.cancel(job['id'])
            self.assertEqual(state['state'], 'cancelling')
            self.manager.worker.join(timeout=2)
        self.assertEqual(process.signals, [signal.SIGINT])
        self.assertEqual(self.manager.status()['state'], 'cancelled')
        with self.assertRaises(web_checks.Conflict):
            self.manager.cancel('another-job')

    def test_real_cli_child_completes_from_cache_without_credentials(self):
        observation = namecheap.unknown('888888.xyz', None)
        observation.update(availability='available', premium=0)
        with catalog.write_lock(self.path):
            catalog.save_observations(self.path, [observation])
        with patch.dict(os.environ, {'NAMECHEAP_USERNAME': '', 'NAMECHEAP_API_KEY': '', 'NAMECHEAP_CLIENT_IP': ''}):
            plan = self.manager.preview({'domains': ['888888.xyz']})
            self.assertEqual(plan['cached'], 1)
            self.assertFalse(plan['configured'])
            self.manager.start(plan['id'])
            self.manager.worker.join(timeout=3)
        self.assertFalse(self.manager.worker.is_alive())
        status = self.manager.status()
        self.assertEqual((status['state'], status['cached'], status['available']), ('completed', 1, 1))
        self.assertIn('requests=0', status['diagnostic'])

    def test_scan_preview_and_command_are_unchecked_only(self):
        before = self.path.read_bytes()
        plan = self.manager.preview_scan({})
        self.assertEqual(plan['selected'], 1)
        self.assertEqual(plan['mode'], 'scan')
        self.assertEqual(plan['request_limits'], {'minute': 40, 'hour': 560, 'day': 6400})
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.manager.preview_scan({'state': 'available'})
        with self.assertRaises(web_checks.Conflict):
            self.manager.start(plan['id'])
        progress = {'total': 1, 'processed': 1, 'remaining': 0, 'requests': 1, 'stop_reason': 'input_exhausted'}
        process = FakeProcess(diagnostic='SCAN_PROGRESS ' + json.dumps(progress) + '\nStop: input_exhausted\n')
        with patch('web_checks.subprocess.Popen', return_value=process) as popen:
            self.manager.start(plan['id'], expected_mode='scan')
            self.manager.worker.join(timeout=2)
        command = popen.call_args.args[0]
        self.assertIn('scan', command)
        self.assertNotIn('--number', command)
        self.assertNotIn('--timeout', command)
        self.assertEqual(self.manager.status()['progress']['remaining'], 0)

    def test_scan_with_no_unchecked_names_does_not_spawn(self):
        observation = namecheap.unknown('888888.xyz', 'test')
        with catalog.write_lock(self.path):
            catalog.save_observations(self.path, [observation])
        plan = self.manager.preview_scan({})
        self.assertEqual(plan['selected'], 0)
        with patch('web_checks.subprocess.Popen') as process, self.assertRaises(web_checks.Conflict):
            self.manager.start(plan['id'], expected_mode='scan')
        process.assert_not_called()

    def test_scan_preview_endpoint_and_api_errors_are_json(self):
        server = viewer.make_server(self.path, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        headers = {'Content-Type':'application/json', 'Origin':f'http://127.0.0.1:{server.server_port}'}
        before = self.path.read_bytes()
        try:
            with patch('web_checks.subprocess.Popen') as process:
                connection.request('POST','/api/scan/preview','{}',headers)
                response=connection.getresponse()
                self.assertEqual(response.status,200)
                self.assertIn('application/json',response.getheader('Content-Type'))
                payload=json.loads(response.read())
                self.assertEqual(payload['selected'],1)
                self.assertEqual(payload['mode'],'scan')
                process.assert_not_called()
            for endpoint,request_headers,status in (
                ('/api/missing',headers,404),('/api/scan/preview',{'Content-Type':'application/json'},403),
                ('/api/scan/preview',{'Origin':headers['Origin'],'Content-Type':'text/plain'},415)):
                connection.request('POST',endpoint,'{}',request_headers)
                response=connection.getresponse()
                self.assertEqual(response.status,status)
                self.assertIn('application/json',response.getheader('Content-Type'))
                self.assertIn('error',json.loads(response.read()))
            self.assertEqual(self.path.read_bytes(),before)
        finally:
            connection.close();server.checks.close();server.shutdown();server.server_close();thread.join()

    def test_preview_expiry_and_catalog_replacement(self):
        plan = self.manager.preview({'domains': ['888888.xyz']})
        with patch('web_checks.time.monotonic', return_value=self.manager.plan[1] + 1), self.assertRaises(web_checks.Conflict):
            self.manager.start(plan['id'])
        self.assertIsNone(self.manager.status())
        replacement = scoring.score('999999', True)
        replacement['rank'] = 1
        catalog.build(self.path, [replacement], {}, {'6': {'examined': 1, 'retained': 1, 'capped': False}}, True)
        with patch('web_checks.subprocess.Popen') as process, self.assertRaises(ValueError):
            self.manager.start(plan['id'])
        process.assert_not_called()

    def test_http_rejects_cross_origin_writes_and_starts_only_reviewed_names(self):
        server = viewer.make_server(self.path, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        origin = f'http://127.0.0.1:{server.server_port}'
        payload = json.dumps({'domains': ['888888.xyz']})
        try:
            for supplied_origin in (None, 'https://untrusted.example'):
                headers = {'Content-Type': 'application/json'}
                if supplied_origin:
                    headers['Origin'] = supplied_origin
                connection.request('POST', '/api/check/preview', payload, headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
            headers = {'Content-Type': 'application/json', 'Origin': origin}
            connection.request('POST', '/api/check/preview', payload, headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            plan = json.loads(response.read())
            with patch('web_checks.subprocess.Popen', return_value=FakeProcess()):
                connection.request('POST', '/api/check/start', json.dumps({'id': plan['id']}), headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                server.checks.worker.join(timeout=2)
            connection.request('GET', '/api/check/status')
            response = connection.getresponse()
            self.assertEqual(json.loads(response.read())['state'], 'completed')
            connection.request('POST', '/api/check/start', json.dumps({'id': plan['id'], 'domains': ['999999.xyz']}), headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
        finally:
            connection.close()
            server.checks.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
