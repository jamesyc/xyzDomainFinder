import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing, redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import catalog
import namecheap
import rate_limit
import scan
import scoring
import web_checks


def make_catalog(path, count=120):
    rows=[]
    for i in range(count):
        result=scoring.score(f'{i:06d}', True)
        result.update(score=count-i, rank=i+1)
        rows.append(result)
    catalog.build(path, rows, {}, {'6':{'examined':count,'retained':count,'capped':False}})


def result(domain, state='available'):
    row=namecheap.unknown(domain, None)
    row.update(availability=state, premium=0)
    return row


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'catalog.sqlite3'
        make_catalog(self.path)

    def run_scan(self, callback, event=None, **options):
        event=event or threading.Event()
        calls=[];progress=[]
        class Client:
            def __init__(self, max_requests, deadline, **kwargs):
                self.requests=0;self.max_requests=max_requests
            def check(self, domains):
                if event.is_set(): raise namecheap.Cancelled()
                if self.requests>=self.max_requests: raise namecheap.BudgetEnded('request_budget')
                self.requests+=1;calls.append(list(domains))
                return callback(list(domains), event, self.requests)
            def wait(self, delay, reason=''):
                if event.is_set(): raise namecheap.Cancelled()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code=scan.run(self.path, stop_event=event, client_factory=Client, progress=progress.append, **options)
        return code,calls,progress

    def test_only_unchecked_and_cancel_then_start_again(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            for label,state in [('000000','available'),('000001','unavailable'),('000002','unknown')]:
                db.execute('UPDATE domains SET availability=?,checked_at=? WHERE label=?',(state,'2000-01-01T00:00:00+00:00',label))
        def first(domains,event,request):
            event.set()
            return {domain:result(domain) for domain in domains}
        code,calls,progress=self.run_scan(first)
        self.assertEqual(code,130)
        self.assertEqual(calls[0][0],'000003.xyz')
        self.assertEqual(len(calls),1)
        self.assertEqual(catalog.unchecked_info(self.path)['total'],67)
        code,calls,progress=self.run_scan(lambda domains,*_: {domain:result(domain,'unavailable') for domain in domains})
        self.assertEqual(code,0)
        self.assertEqual(calls[0][0],'000053.xyz')
        self.assertEqual(len(calls),2)
        self.assertEqual(catalog.unchecked_info(self.path)['total'],0)

    def test_cancel_without_response_leaves_unchecked(self):
        def cancelled(domains,event,request):
            event.set();raise namecheap.Cancelled()
        code,calls,progress=self.run_scan(cancelled)
        self.assertEqual(code,130)
        self.assertEqual(catalog.unchecked_info(self.path)['total'],120)
        event=threading.Event();event.set()
        code,calls,_=self.run_scan(cancelled,event)
        self.assertEqual(calls,[])
        def interrupted_timeout(domains,event,request):
            event.set();raise namecheap.APIError('network_error',True)
        code,_,_=self.run_scan(interrupted_timeout)
        self.assertEqual(code,130)
        self.assertEqual(catalog.unchecked_info(self.path)['total'],120)

    def test_real_process_signal_keeps_committed_batch(self):
        program = '''
import sys
import namecheap, scan
class Client:
    def __init__(self,max_requests,deadline,stop_event=None,progress=None):
        self.requests=0;self.max_requests=max_requests;self.event=stop_event
    def check(self,domains):
        self.requests+=1
        if self.requests>1:
            self.event.wait(10)
            raise namecheap.Cancelled()
        rows={domain:namecheap.unknown(domain,None) for domain in domains}
        for row in rows.values():row['availability']='available'
        return rows
    def wait(self,*args):pass
def progress(state):
    if state['processed']==50:print('READY',file=sys.stderr,flush=True)
raise SystemExit(scan.run(sys.argv[1],client_factory=Client,progress=progress))
'''
        process=subprocess.Popen([sys.executable,'-c',program,str(self.path)],stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE,text=True,cwd=Path(__file__).parent)
        ready=threading.Event()
        def read():
            for line in process.stderr:
                if line.strip()=='READY':ready.set()
        reader=threading.Thread(target=read,daemon=True);reader.start()
        try:
            self.assertTrue(ready.wait(3),'worker did not commit its first batch')
            process.send_signal(signal.SIGINT)
            self.assertEqual(process.wait(timeout=3),130)
            self.assertEqual(catalog.unchecked_info(self.path)['total'],70)
        finally:
            if process.poll() is None:process.kill();process.wait()
            reader.join(timeout=1);process.stderr.close()

    def test_partial_responses_retry_unresolved_only(self):
        def partial(domains,event,request):
            if request==1:
                return {d:result(d) if i<25 else namecheap.unknown(d,'missing') for i,d in enumerate(domains)}
            return {d:result(d) for d in domains}
        code,calls,_=self.run_scan(partial,max_checks=50)
        self.assertEqual([len(c) for c in calls],[50,25])
        self.assertEqual(calls[1],calls[0][25:])
        self.assertEqual(catalog.unchecked_info(self.path)['total'],70)

    def test_transient_outage_stops_after_one_failed_batch(self):
        def failed(*_): raise namecheap.APIError('http_503',True)
        code,calls,_=self.run_scan(failed)
        self.assertEqual((code,len(calls)),(1,3))
        self.assertEqual(catalog.unchecked_info(self.path)['total'],70)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM domains WHERE availability='unknown'").fetchone()[0],50)

    def test_auth_failure_and_failed_commit_do_not_advance(self):
        def failed(*_): raise namecheap.APIError('api_1017105')
        code,calls,_=self.run_scan(failed)
        self.assertEqual((code,len(calls)),(1,1))
        self.assertEqual(catalog.unchecked_info(self.path)['total'],120)
        with patch('catalog.save_observations',side_effect=sqlite3.OperationalError('disk full')):
            code,_,_=self.run_scan(lambda domains,*_: {d:result(d) for d in domains})
        self.assertEqual(code,1)
        self.assertEqual(catalog.unchecked_info(self.path)['total'],120)

    def test_request_budget_keeps_unresolved_unchecked(self):
        code,calls,_=self.run_scan(lambda domains,*_: {d:namecheap.unknown(d,'missing') for d in domains},max_requests=1)
        self.assertEqual((code,len(calls)),(0,1))
        self.assertEqual(catalog.unchecked_info(self.path)['total'],120)

    def test_terminal_unknown_is_not_revisited_on_next_start(self):
        code,calls,_=self.run_scan(lambda domains,*_: {d:namecheap.unknown(d,'missing_result') for d in domains},max_checks=50)
        self.assertEqual((code,len(calls)),(0,3))
        self.assertEqual(catalog.unchecked_info(self.path)['total'],70)
        code,calls,_=self.run_scan(lambda domains,*_: {d:result(d) for d in domains})
        self.assertEqual(code,0)
        self.assertEqual(calls[0][0],'000050.xyz')
        self.assertEqual(catalog.unchecked_info(self.path)['total'],0)

    def test_rate_wait_is_interruptible_and_reports_reason(self):
        event=threading.Event();updates=[]
        with patch.dict(os.environ,{'NAMECHEAP_USERNAME':'test','NAMECHEAP_API_KEY':'test','NAMECHEAP_CLIENT_IP':'192.0.2.1'}):
            client=namecheap.Client(10,float('inf'),ledger=object(),stop_event=event,progress=lambda **state:(updates.append(state),event.set()))
        with self.assertRaises(namecheap.Cancelled): client.wait(3600,'hour quota')
        self.assertEqual(updates[0]['wait_reason'],'hour quota')


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'rate.sqlite3';self.clock=[100000.0]
        self.ledger=rate_limit.Ledger('account',self.path,lambda:self.clock[0])

    def test_spacing_and_provider_wait_survive_new_clients(self):
        self.assertEqual(self.ledger.reserve()[0],0)
        second=rate_limit.Ledger('ACCOUNT',self.path,lambda:self.clock[0])
        self.assertEqual(second.reserve(),(1.5,'request spacing'))
        self.clock[0]+=1.5;self.assertEqual(second.reserve()[0],0)
        second.defer(300)
        third=rate_limit.Ledger('account',self.path,lambda:self.clock[0])
        self.assertEqual(third.reserve(),(300,'provider backoff'))
        self.assertEqual(rate_limit.Ledger('another',self.path,lambda:self.clock[0]).reserve()[0],0)

    def test_each_window_stays_at_eighty_percent(self):
        self.assertEqual([limit for _,limit,_ in rate_limit.WINDOWS],[40,560,6400])
        for window,limit,label in rate_limit.WINDOWS:
            with self.subTest(window=window):
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute('DELETE FROM requests')
                    db.executemany('INSERT INTO requests VALUES (?,?)',[(self.ledger.account,self.clock[0]-window+10)]*limit)
                delay,reason=self.ledger.reserve()
                self.assertEqual((delay,reason),(10,label))
                self.clock[0]+=10
                self.assertEqual(self.ledger.reserve()[0],0)

    def test_reservations_are_atomic(self):
        barrier=threading.Barrier(2);results=[]
        def reserve():
            ledger=rate_limit.Ledger('account',self.path,lambda:self.clock[0])
            barrier.wait();results.append(ledger.reserve()[0])
        threads=[threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(sorted(results),[0,1.5])


if __name__=='__main__': unittest.main()
