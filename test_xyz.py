import csv
import io
import json
import random
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import date
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

import candidates
import catalog
import scoring
import viewer
import xyz


def cli(*arguments):
    output,errors=io.StringIO(),io.StringIO()
    with redirect_stdout(output),redirect_stderr(errors):
        try: status=xyz.main(list(arguments))
        except SystemExit as error: status=error.code
    return status,output.getvalue(),errors.getvalue()


def row(label,rank=1):
    result=scoring.score(label,True);result['rank']=rank
    return result


def make_catalog(path):
    rows=[row('888888'),row('121212',2),row('0000000'),row('12345678'),row('123456789')]
    stats={str(n):{'examined':20,'retained':sum(r['length']==n for r in rows),'capped':False,'cutoff':1} for n in (6,7,8,9)}
    catalog.build(path,rows,{},stats)
    return rows,stats


class ScoringTests(unittest.TestCase):
    def test_worked_examples(self):
        for label,expected in {'888888':80,'121212':71,'112233':66,'100000':59,'123456':60,'123321':71,'123456789':60,'314159265':55}.items():
            with self.subTest(label=label):
                result=scoring.score(label,True)
                self.assertEqual(result['score'],expected)
                self.assertEqual(sum(p['awarded'] for p in result['properties']),expected)
        props={p['id']:p for p in scoring.score('888888',True)['properties']}
        self.assertEqual(props['palindrome']['awarded'],0)
        self.assertEqual(props['uniform']['awarded'],60)

    def test_fast_scoring_matches_explanations(self):
        randomizer=random.Random(42)
        examples=['121213','111222333','27182818','20240229','20230229','12000000']
        examples.extend(''.join(randomizer.choices('0123456789',k=n)) for n in (6,7,8,9) for _ in range(100))
        for label in examples:
            self.assertEqual(scoring.score(label),scoring.score(label,True)['score'],label)
            for prop in scoring.score(label,True)['properties']:
                self.assertTrue(prop['evidence'])

    def test_dates_near_repeats_and_non_wrapping_sequences(self):
        self.assertIn('date',scoring.score('20240229',True)['reasons'])
        self.assertNotIn('date',scoring.score('20230229',True)['reasons'])
        self.assertNotIn('sequence',scoring.score('789012',True)['reasons'])
        self.assertIn('near_repeat',scoring.score('121213',True)['reasons'])
        self.assertEqual(list(candidates.labels('date',8,date(9999,12,31),date(9999,12,31))),['99991231'])

    def test_heap_order_is_stable_with_duplicates(self):
        names=['888888','123456','121212','123321','100000','000000']
        a,_=candidates.select(6,3,patterns=[],explicit=names*3)
        b,_=candidates.select(6,3,patterns=[],explicit=list(reversed(names))*2)
        expected=sorted(names,key=scoring.order_key)[:3]
        self.assertEqual([r['domain'][:-4] for r in a],expected)
        self.assertEqual(a,b)

    def test_composed_patterns_are_rewarded_without_stacking_aliases(self):
        cases = {'111222333': (56, 'stepping_runs'), '123123': (81, 'motif_sequence'),
                 '1234321': (65, 'mirrored_sequence'), '123454321': (65, 'mirrored_sequence'),
                 '123124125': (45, 'counting_blocks'), '122333': (66, 'staircase'),
                 '112223333': (66, 'staircase')}
        for label, (expected, property_id) in cases.items():
            result = scoring.score(label, True)
            self.assertEqual(result['score'], expected, label)
            self.assertEqual(scoring.score(label), expected, label)
            self.assertIn(property_id, result['reasons'].split(';'))
            self.assertEqual(sum(p['awarded'] for p in result['properties']), expected)
        result = scoring.score('112233', True)
        self.assertEqual(sum(p['awarded'] for p in result['properties'] if p['family'] == 'progression'), 30)
        for label, rule in [('121212','motif_sequence'), ('1294921','mirrored_sequence'),
                            ('111333555','stepping_runs'), ('122334','staircase')]:
            self.assertNotIn(rule, scoring.score(label, True)['reasons'].split(';'))

    def test_chunk_coverage_and_composed_pattern_sources(self):
        chunks = list(candidates.labels('chunks', 9))
        self.assertIn('111222333', chunks)
        self.assertEqual(len(chunks), len(set(chunks)))
        self.assertTrue(all('chunks' in scoring.score(label, True)['reasons'].split(';') for label in chunks))
        self.assertIn('122333', list(candidates.staircases(6)))
        found, _ = candidates.select(6, 100, patterns=['motif_sequence','mirrored_sequence'])
        properties = {p['id'] for row in found for p in row['properties']}
        self.assertTrue({'motif_sequence','mirrored_sequence'} <= properties)

    def test_equal_scores_prefer_simpler_runs(self):
        labels = ['100101001', '110000011']
        self.assertEqual(scoring.score(labels[0]), scoring.score(labels[1]))
        selected, _ = candidates.select(9, 2, patterns=[], explicit=labels)
        self.assertEqual(selected[0]['domain'], '110000011.xyz')

    def test_filters_caps_and_leading_zero_identity(self):
        selected,stats=candidates.select(6,20,patterns=['repeat'],prefix='12',suffix='12',contains='21')
        self.assertEqual([r['domain'] for r in selected],['121212.xyz'])
        selected,stats=candidates.select(9,100,patterns=['palindrome'],max_generated=10)
        self.assertEqual(stats['examined'],10)
        self.assertTrue(stats['capped'])
        self.assertLessEqual(len(selected),10)
        self.assertEqual(xyz.numeric_label(' 001234.XYZ '),'001234')
        self.assertEqual(xyz.numeric_label('0001234'),'0001234')

    def test_cli_validation_and_score(self):
        for invalid in ('１２３４５６','123 456','12345','123456.com'):
            self.assertEqual(cli('score',invalid)[0],2)
        self.assertEqual(cli('build','--keep','10')[0],2)
        self.assertEqual(cli('build','--keep-per-length','0')[0],2)
        self.assertEqual(cli('generate','--pattern','pair','--length','7')[0],2)
        result=json.loads(cli('score','888888.xyz')[1])[0]
        self.assertEqual(result['score'],80)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'names.txt';path.write_text('001234\ninvalid\n')
            status,output,errors=cli('generate','--input',str(path))
            self.assertEqual((status,output),(2,''))
            self.assertIn(':2:',errors)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'catalog.sqlite3'

    def test_default_lengths_and_scored_storage_offline(self):
        with patch('socket.socket',side_effect=AssertionError('network access')):
            status,_,_=cli('build','--database',str(self.path),'--keep-per-length','5','--pattern','uniform')
        self.assertEqual(status,0)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT length,count(*),min(rank),max(rank) FROM domains GROUP BY length').fetchall(),[(n,5,1,5) for n in (6,7,8,9)])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM domains WHERE availability!='unchecked' OR checked_at IS NOT NULL").fetchone()[0],0)
            for score,properties in db.execute('SELECT score,properties_json FROM domains'):
                self.assertEqual(score,sum(p['awarded'] for p in json.loads(properties)))
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_query_detail_and_summary_agree(self):
        make_catalog(self.path)
        page=catalog.page(self.path,{'length':6,'min_score':70},1,1)
        self.assertEqual(page['total'],2)
        self.assertEqual(page['rows'][0]['domain'],'121212.xyz')
        self.assertNotIn('properties_json',page['rows'][0])
        details=catalog.detail(self.path,'121212.xyz')
        self.assertEqual(details['score'],sum(p['awarded'] for p in details['properties']))
        self.assertEqual(catalog.page(self.path,{'pattern':['date']})['total'],1)
        self.assertEqual(catalog.page(self.path,{'search':'0000000'})['rows'][0]['length'],7)
        self.assertEqual(catalog.summary(self.path)['row_count'],5)
        self.assertEqual(len(list(catalog.export_rows(self.path,{'length':6}))),2)

    def test_reuse_replace_and_failed_publication(self):
        rows,stats=make_catalog(self.path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE domains SET availability='available',provider='test',registration_price='0.99' WHERE domain='888888.xyz'")
        self.assertFalse(catalog.build(self.path,rows,{},stats))
        with self.assertRaises(ValueError): catalog.build(self.path,rows,{'different':True},stats)
        before=self.path.read_bytes()
        with self.assertRaises(sqlite3.IntegrityError): catalog.build(self.path,rows+[rows[0]],{},stats,True)
        self.assertEqual(before,self.path.read_bytes())
        self.assertFalse(list(self.path.parent.glob('.catalog-*.sqlite3')))
        catalog.build(self.path,[rows[0]],{}, {'6':stats['6']},True)
        self.assertEqual(catalog.detail(self.path,'888888.xyz')['registration_price'],'0.99')

    def test_repeated_year_collection_boundaries_status_paging_and_export(self):
        names=[str(year)*2 for year in range(1900,2050)]
        labels=names+['18991899','20502050','19001901','1900190','190019000']
        rows=[row(label,index) for index,label in enumerate(labels,1)]
        stats={str(n):{'examined':len(rows),'retained':sum(r['length']==n for r in rows),'capped':False} for n in (6,7,8,9)}
        catalog.build(self.path,rows,{},stats)
        filters=viewer.query_options('collection=repeated_years')
        first=catalog.page(self.path,filters,100)
        second=catalog.page(self.path,filters,100,100)
        self.assertEqual(first['total'],150)
        self.assertEqual({r['domain'] for r in first['rows']+second['rows']},{name+'.xyz' for name in names})
        self.assertEqual(len(list(catalog.export_rows(self.path,filters))),150)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE domains SET availability='available' WHERE domain IN ('19001900.xyz','20502050.xyz')")
        filters=viewer.query_options('collection=repeated_years&state=available')
        self.assertEqual([r['domain'] for r in viewer.snapshot(self.path,filters)['rows']],['19001900.xyz'])
        self.assertEqual([r['domain'] for r in catalog.export_rows(self.path,filters)],['19001900.xyz'])
        self.assertEqual(catalog.page(self.path,{'collection':'repeated_years','length':7})['total'],0)

    def test_migrate_v1_preserves_observations(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE domains(domain TEXT,availability TEXT,checked_at TEXT,provider TEXT,registration_price TEXT,renewal_price TEXT,currency TEXT);')
            db.executemany('INSERT INTO metadata VALUES (?,?)',[('ranking_version','pattern-shortlist-v1'),('row_count','1')])
            db.execute("INSERT INTO domains VALUES ('888888.xyz','available','2026-09-11','test','0.99','0.99','USD')")
        catalog.build(self.path,[row('888888')],{}, {'6':{'examined':1,'retained':1,'capped':False}},True)
        result=catalog.detail(self.path,'888888.xyz')
        self.assertEqual((result['score'],result['availability'],result['currency']),(80,'available','USD'))

    def test_missing_database_does_not_create_file(self):
        self.assertEqual(cli('find','--database',str(self.path))[0],1)
        self.assertFalse(self.path.exists())


class ViewerTests(unittest.TestCase):
    def test_paging_filters_details_export_and_read_only_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'catalog.sqlite3';make_catalog(path)
            server=viewer.make_server(path,0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            connection=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            try:
                for asset in ('/','/app.js','/style.css','/favicon.svg'):
                    connection.request('GET',asset);response=connection.getresponse()
                    self.assertEqual(response.status,200);self.assertTrue(response.read())
                connection.request('GET','/api/catalog?length=6&min_score=70&page_size=1&page=2')
                response=connection.getresponse();payload=json.loads(response.read())
                self.assertEqual((payload['total'],len(payload['rows']),payload['metadata']['row_count']),(2,1,5))
                self.assertNotIn('properties_json',payload['rows'][0])
                connection.request('GET','/api/domain?domain=888888.xyz')
                response=connection.getresponse();self.assertEqual(json.loads(response.read())['score'],80)
                connection.request('GET','/export.csv?length=6&min_score=70&page_size=1')
                response=connection.getresponse();self.assertIn('attachment',response.getheader('Content-Disposition'))
                self.assertEqual(len(list(csv.DictReader(io.StringIO(response.read().decode())))),2)
                for endpoint in ('/api/catalog?page=-1','/api/catalog?length=10','/api/catalog?pattern=bad','/api/catalog?collection=bad','/api/catalog?page_size=1000','/api/catalog?min_score=-1'):
                    connection.request('GET',endpoint);response=connection.getresponse()
                    self.assertEqual(response.status,400);response.read()
                for endpoint in ('/.env','/domains.sqlite3','/../xyz.py'):
                    connection.request('GET',endpoint);response=connection.getresponse()
                    self.assertEqual(response.status,404);response.read()
                connection.request('POST','/api/catalog',body='{}');response=connection.getresponse()
                self.assertEqual(response.status,403);response.read()
                connection.request('GET','/api/catalog',headers={'Host':'untrusted.example'});response=connection.getresponse()
                self.assertEqual(response.status,403);response.read()
            finally:
                connection.close();server.shutdown();server.server_close();thread.join()


if __name__ == '__main__': unittest.main()
