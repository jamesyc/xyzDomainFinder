import csv
import io
import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import catalog
import namecheap
import scoring
import xyz


def entry(domain, available='true', premium='false', **extra):
    attributes={'Domain':domain,'Available':available,'IsPremiumName':premium,'ErrorNo':'0',**extra}
    return '<DomainCheckResult '+' '.join(f'{k}="{v}"' for k,v in attributes.items())+'/>'


def response(*entries, error=None):
    if error:
        return f'<ApiResponse Status="ERROR"><Errors><Error Number="{error}">test-secret</Error></Errors></ApiResponse>'.encode()
    return ('<ApiResponse xmlns="http://api.namecheap.com/xml.response" Status="OK">'
            '<RequestedCommand>namecheap.domains.check</RequestedCommand><CommandResponse>'
            +''.join(entries)+'</CommandResponse></ApiResponse>').encode()


class NamecheapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'catalog.sqlite3'
        labels=['888888','121212','123456789']
        rows=[]
        for label,rank in zip(labels,[1,2,1]):
            row=scoring.score(label,True);row['rank']=rank;rows.append(row)
        catalog.build(self.path,rows,{}, {'6':{'examined':2,'retained':2,'capped':False},'9':{'examined':1,'retained':1,'capped':False}})
        self.domains=[label+'.xyz' for label in labels]

    def run_checks(self, responses, **options):
        args=Namespace(limit=200,target=10,max_checks=50,max_requests=20,timeout=60,
                       cache_minutes=15,refresh=False,exclude_premium=False,
                       max_registration=None,max_renewal=None,currency=None)
        args.__dict__.update(options)
        queue=iter(responses);requests=[];clock=[0.0]
        output,errors=io.StringIO(),io.StringIO()

        def send(request, timeout):
            query=parse_qs(urlsplit(request.full_url).query)
            self.assertEqual(query['Command'],['namecheap.domains.check'])
            requests.append(query['DomainList'][0].split(','))
            item=next(queue)
            if isinstance(item, BaseException): raise item
            return io.BytesIO(item)

        def factory(max_requests, deadline):
            client=namecheap.Client(max_requests,deadline)
            client.opener=Mock(open=Mock(side_effect=send))
            return client

        with patch.dict(os.environ,{'NAMECHEAP_USERNAME':'test','NAMECHEAP_API_KEY':'test-secret','NAMECHEAP_CLIENT_IP':'192.0.2.1'}), \
             patch.object(namecheap.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(namecheap.time,'sleep',side_effect=lambda delay:clock.__setitem__(0,clock[0]+delay)), \
             redirect_stdout(output), redirect_stderr(errors):
            status=namecheap.run(self.path,args,self.domains,client_factory=factory)
        return status,list(csv.DictReader(io.StringIO(output.getvalue()))),errors.getvalue(),requests

    def test_success_saves_and_next_run_reuses_without_requests(self):
        status,rows,summary,requests=self.run_checks([response(*(entry(d) for d in self.domains))],target=3)
        self.assertEqual((status,len(rows),len(requests)),(0,3,1))
        for domain in self.domains:
            saved=catalog.detail(self.path,domain)
            self.assertEqual(saved['availability'],'available')
            self.assertEqual(saved['provider'],'namecheap')
            self.assertIsNone(saved['registration_price'])
        status,rows,summary,requests=self.run_checks([],target=3)
        self.assertEqual(requests,[])
        self.assertIn('target_reached_from_cache',summary)
        self.assertTrue(all(row['cached']=='True' for row in rows))

    def test_partial_response_retries_only_unresolved(self):
        status,rows,summary,requests=self.run_checks([
            response(entry(self.domains[0])),response(entry(self.domains[1]),entry(self.domains[2]))
        ],target=3)
        self.assertEqual(requests,[self.domains,self.domains[1:]])
        self.assertEqual(status,0)
        self.assertEqual(len(rows),3)
        self.assertIn('live_names=3',summary)

    def test_name_and_request_budgets_do_not_mark_unvisited(self):
        status,rows,summary,requests=self.run_checks([response(entry(self.domains[0],'false'))],max_checks=1)
        self.assertEqual(requests,[[self.domains[0]]])
        self.assertIn('name_budget',summary)
        self.assertEqual(catalog.detail(self.path,self.domains[1])['availability'],'unchecked')
        status,rows,summary,requests=self.run_checks([HTTPError('https://test.invalid/?test-secret',429,'test-secret',{'Retry-After':'2'},None)],max_requests=1,refresh=True)
        self.assertEqual(len(requests),1)
        self.assertIn('request_budget',summary)
        self.assertNotIn('test-secret',summary)
        self.assertTrue(all(row['availability']=='unknown' for row in rows))

    def test_retry_after_deadline_and_auth_failures(self):
        failure=HTTPError('https://test.invalid',429,'ignored',{'Retry-After':'300'},None)
        status,rows,summary,requests=self.run_checks([failure])
        self.assertEqual(len(requests),1)
        self.assertIn('deadline',summary)
        self.assertEqual(rows[0]['check_error'],'http_429')
        status,rows,summary,requests=self.run_checks([response(error='1017105')])
        self.assertEqual(status,1)
        self.assertEqual(len(requests),1)
        self.assertNotIn('test-secret',summary)
        self.assertIn('api_1017105',summary)

    def test_malformed_and_duplicate_results_remain_unknown(self):
        domain=self.domains[0]
        for body in (b'not XML',response(entry('999999.xyz')),response(entry(domain),entry(domain))):
            with self.subTest(body=body):
                status,rows,summary,requests=self.run_checks([body,body,body],max_checks=1,refresh=True)
                self.assertEqual(len(requests),3)
                self.assertEqual(rows[0]['availability'],'unknown')
                self.assertEqual(catalog.detail(self.path,domain)['availability'],'unknown')

    def test_interruption_persists_completed_and_attempted_rows(self):
        status,rows,summary,requests=self.run_checks([KeyboardInterrupt()])
        self.assertEqual(status,130)
        self.assertEqual(len(rows),3)
        self.assertTrue(all(row['check_error']=='interrupted_request' for row in rows))

    def test_freshness_and_price_eligibility(self):
        now=datetime.now(timezone.utc)
        row=namecheap.unknown(self.domains[0],None)
        row.update(availability='available',premium=0,checked_at=now.isoformat())
        self.assertTrue(namecheap.fresh(row,15,now))
        row['checked_at']=(now-timedelta(minutes=16)).isoformat()
        self.assertFalse(namecheap.fresh(row,15,now))
        row['checked_at']=(now+timedelta(minutes=1)).isoformat()
        self.assertFalse(namecheap.fresh(row,15,now))
        args=Namespace(exclude_premium=False,max_registration=None,max_renewal=None,currency='USD')
        self.assertTrue(namecheap.eligible(row,args))
        row['premium']=1;self.assertTrue(namecheap.eligible(row,args))
        args.exclude_premium=True;self.assertFalse(namecheap.eligible(row,args))
        row['premium']=0;args.max_registration=Decimal('1')
        self.assertFalse(namecheap.eligible(row,args))
        row.update(registration_price='0.99',term_years=1,currency='USD')
        self.assertTrue(namecheap.eligible(row,args))
        row['currency']='EUR';self.assertFalse(namecheap.eligible(row,args))
        parsed=namecheap.parse_response(response(entry(self.domains[0],premium='true',PremiumRegistrationPrice='0.99',PremiumRenewalPrice='NaN')),self.domains[:1])[self.domains[0]]
        self.assertEqual(parsed['registration_price'],'0.99')
        self.assertIsNone(parsed['renewal_price']);self.assertIsNone(parsed['currency'])

    def test_preview_needs_no_credentials_or_writes(self):
        before=self.path.read_bytes()
        with patch.dict(os.environ,{},clear=True), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(xyz.main(['check','--database',str(self.path),'--number',self.domains[0],'--preview']),0)
        self.assertEqual(before,self.path.read_bytes())
        with catalog.write_lock(self.path):
            with self.assertRaisesRegex(ValueError,'Another catalog'):
                with catalog.write_lock(self.path): pass

    def test_retry_after_formats(self):
        self.assertEqual(namecheap.retry_after('10'),10)
        self.assertGreater(namecheap.retry_after('Wed, 01 Jan 2098 00:00:00 GMT'),60)
        for value in ('NaN','Infinity','-1',None,'invalid'):
            self.assertIsNone(namecheap.retry_after(value))


if __name__=='__main__': unittest.main()
