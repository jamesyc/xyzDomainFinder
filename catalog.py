"""Atomic scored catalogs and shared, parameterized browsing queries."""

import fcntl
import json
import os
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import scoring

RANKING_VERSION = scoring.VERSION
EXTRA_OBSERVATIONS = {'premium': 'INTEGER', 'term_years': 'INTEGER', 'icann_fee': 'TEXT',
                      'eap_fee': 'TEXT', 'quote_note': 'TEXT', 'check_error': 'TEXT'}
OBSERVATIONS = ('availability','checked_at','provider','registration_price','renewal_price','currency',
                *EXTRA_OBSERVATIONS)
COLUMNS = ('domain','length','score','rank','reasons',*OBSERVATIONS)
SCHEMA = '''
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE domains (
 domain TEXT PRIMARY KEY,
 label TEXT NOT NULL UNIQUE CHECK(label NOT GLOB '*[^0-9]*'),
 length INTEGER NOT NULL CHECK(length BETWEEN 6 AND 9 AND length=length(label)),
 score INTEGER NOT NULL CHECK(score >= 0),
 rank INTEGER NOT NULL CHECK(rank > 0),
 reasons TEXT NOT NULL,
 properties_json TEXT NOT NULL CHECK(json_valid(properties_json)),
 availability TEXT NOT NULL DEFAULT 'unchecked' CHECK(availability IN ('unchecked','available','unavailable','unknown')),
 checked_at TEXT, provider TEXT, registration_price TEXT, renewal_price TEXT, currency TEXT,
 premium INTEGER, term_years INTEGER, icann_fee TEXT, eap_fee TEXT, quote_note TEXT, check_error TEXT,
 CHECK(domain=label||'.xyz')
);
CREATE UNIQUE INDEX domains_length_rank ON domains(length,rank);
CREATE INDEX domains_length_score ON domains(length,score DESC,rank);
CREATE INDEX domains_score ON domains(score DESC,length,rank);
'''


def open_catalog(path, legacy=False):
    connection=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        metadata=dict(connection.execute('SELECT key,value FROM metadata'))
        allowed={RANKING_VERSION,'numeric-interest-v1','pattern-shortlist-v1'} if legacy else {RANKING_VERSION}
        if metadata.get('ranking_version') not in allowed:
            raise ValueError('Catalog uses an older scoring version; run build --replace')
        if int(metadata.get('row_count',-1)) != connection.execute('SELECT COUNT(*) FROM domains').fetchone()[0]:
            raise ValueError('Incomplete catalog; rebuild it with --replace')
    except BaseException:
        connection.close()
        raise
    return connection


def select_columns(connection, columns=COLUMNS):
    """Read earlier catalogs without requiring a write merely to view them."""
    existing = {row[1] for row in connection.execute('PRAGMA table_info(domains)')}
    return ','.join(column if column in existing else f'NULL AS {column}' for column in columns)


@contextmanager
def write_lock(path):
    """Coordinate rebuild publication and checks so neither loses the other's writes."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name('.catalog-' + path.name + '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another catalog build or check is running; try again when it finishes') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def build(path, rows, selection, stats, replace=False):
    with write_lock(path):
        return _build(path, rows, selection, stats, replace)


def ensure_observation_schema(path):
    """Add nullable observation fields in place; caller holds write_lock."""
    with closing(open_catalog(path)):
        pass
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True)) as connection:
        with connection:
            columns = {row[1] for row in connection.execute('PRAGMA table_info(domains)')}
            for column, kind in EXTRA_OBSERVATIONS.items():
                if column not in columns:
                    connection.execute(f'ALTER TABLE domains ADD COLUMN {column} {kind}')
            connection.execute("UPDATE metadata SET value='3' WHERE key='schema_version'")


def save_observations(path, observations):
    """Commit each response batch independently; caller holds write_lock."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True)) as connection:
        with connection:
            for result in observations:
                updated = connection.execute('UPDATE domains SET ' + ','.join(f'{column}=?' for column in OBSERVATIONS)
                                             + ' WHERE domain=?',
                                             [*(result.get(column) for column in OBSERVATIONS), result['domain']])
                if updated.rowcount != 1:
                    raise ValueError('A checked candidate disappeared from the catalog')


def check_selection(path, filters, limit, domains=None):
    clause, params = where(filters)
    with closing(open_catalog(path)) as connection:
        connection.row_factory = sqlite3.Row
        if domains is not None:
            if not domains:
                return []
            placeholders = ','.join('?' for _ in domains)
            existing = {row[0] for row in connection.execute('SELECT domain FROM domains WHERE domain IN (' + placeholders + ')', domains)}
            missing = [domain for domain in domains if domain not in existing]
            if missing:
                raise ValueError('Names are not in the catalog: ' + ', '.join(missing[:5]))
            clause += (' AND ' if clause else ' WHERE ') + 'domain IN (' + placeholders + ')'
            params.extend(domains)
        query = 'SELECT ' + select_columns(connection) + ' FROM domains' + clause + ' ORDER BY score DESC,length,rank'
        if domains is None:
            query += ' LIMIT ?'
            params.append(limit)
        rows = [dict(row) for row in connection.execute(query, params)]
    if domains is not None:
        order = {domain: index for index, domain in enumerate(domains)}
        rows.sort(key=lambda row: order[row['domain']])
    return rows[:limit]


def _build(path, rows, selection, stats, replace=False):
    path=Path(path)
    settings=json.dumps(selection,sort_keys=True)
    retained={row['domain'] for row in rows}
    observations={}
    exists=path.exists()
    if exists:
        with closing(open_catalog(path,legacy=True)) as connection:
            metadata=dict(connection.execute('SELECT key,value FROM metadata'))
            if metadata.get('ranking_version') == RANKING_VERSION and metadata.get('selection') == settings and not replace:
                return False
            if not replace:
                raise ValueError('Catalog settings/version differ; use --replace or another --database')
            for domain,*values in connection.execute('SELECT '+select_columns(connection, ('domain', *OBSERVATIONS))+' FROM domains'):
                if domain in retained:
                    observations[domain]=values
    pattern_counts=Counter(prop['id'] for row in rows for prop in row['properties'])
    metadata={
        'ranking_version':RANKING_VERSION,'schema_version':'3','selection':settings,
        'profile':json.dumps(scoring.PROFILE),'cohorts':json.dumps(stats),
        'pattern_counts':json.dumps(pattern_counts),'row_count':str(len(rows)),
        'examined':str(sum(info['examined'] for info in stats.values())),
        'cap_reached':str(any(info['capped'] for info in stats.values())).lower(),
        'coverage':'structured candidate pool; no exhaustive namespace claim',
        'created_at':datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(prefix='.catalog-',suffix='.sqlite3',dir=path.parent)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.executescript(SCHEMA)
            with connection:
                connection.executemany('INSERT INTO domains VALUES ('+','.join('?' for _ in range(7+len(OBSERVATIONS)))+')',(
                    (row['domain'],row['domain'][:-4],row['length'],row['score'],row['rank'],row['reasons'],
                     json.dumps(row['properties'],separators=(',',':')),
                     *observations.get(row['domain'],('unchecked',)+(None,)*(len(OBSERVATIONS)-1))) for row in rows))
                connection.executemany('INSERT INTO metadata VALUES (?,?)',metadata.items())
            connection.execute('PRAGMA optimize')
        if exists:
            os.replace(temporary,path)
        else:
            os.link(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def where(filters):
    clauses,params=[],[]
    patterns=filters.get('pattern') or []
    if isinstance(patterns,str): patterns=[patterns]
    if patterns:
        clauses.append('('+' OR '.join("instr(';'||reasons||';',?)>0" for _ in patterns)+')')
        params.extend(';'+pattern+';' for pattern in patterns)
    lengths=filters.get('length') or []
    if isinstance(lengths,(int,str)): lengths=[lengths]
    if lengths:
        clauses.append('length IN ('+','.join('?' for _ in lengths)+')')
        params.extend(lengths)
    for key,clause,transform in (
        ('search','instr(domain,?)>0',lambda v:v.strip().lower()),
        ('prefix','label LIKE ?',lambda v:v+'%'),('suffix','label LIKE ?',lambda v:'%'+v),
        ('contains','instr(label,?)>0',lambda v:v),('state','availability=?',lambda v:v),
    ):
        if filters.get(key):
            clauses.append(clause);params.append(transform(filters[key]))
    if filters.get('no_leading_zero'): clauses.append("label NOT LIKE '0%'")
    if filters.get('min_score') is not None:
        clauses.append('score>=?');params.append(filters['min_score'])
    return (' WHERE '+' AND '.join(clauses) if clauses else ''),params


def find(path,args):
    return page(path,vars(args),args.limit,0)['rows']


def page(path,filters,limit=20,offset=0):
    clause,params=where(filters)
    with closing(open_catalog(path)) as connection:
        connection.row_factory=sqlite3.Row
        total=connection.execute('SELECT COUNT(*) FROM domains'+clause,params).fetchone()[0]
        rows=[dict(row) for row in connection.execute('SELECT '+select_columns(connection)+' FROM domains'+clause+
               ' ORDER BY score DESC,length,rank LIMIT ? OFFSET ?',[*params,limit,offset])]
    return {'rows':rows,'total':total}


def detail(path,domain):
    with closing(open_catalog(path)) as connection:
        connection.row_factory=sqlite3.Row
        row=connection.execute('SELECT '+select_columns(connection)+',properties_json FROM domains WHERE domain=?',(domain,)).fetchone()
    if row is None: return None
    result=dict(row);result['properties']=json.loads(result.pop('properties_json'))
    result['tie_break']=scoring.complexity(domain[:-4])
    return result


def summary(path):
    with closing(open_catalog(path)) as connection:
        metadata=dict(connection.execute('SELECT key,value FROM metadata'))
        checked=connection.execute("SELECT COUNT(*) FROM domains WHERE availability!='unchecked'").fetchone()[0]
    return {'row_count':int(metadata['row_count']),'examined':int(metadata['examined']),
            'checked':checked,'cohorts':json.loads(metadata['cohorts']),
            'pattern_counts':json.loads(metadata['pattern_counts']),
            'ranking_version':metadata['ranking_version'],'created_at':metadata['created_at'],
            'cap_reached':metadata['cap_reached'],'coverage':metadata['coverage']}


def export_rows(path,filters):
    clause,params=where(filters)
    with closing(open_catalog(path)) as connection:
        connection.row_factory=sqlite3.Row
        for row in connection.execute('SELECT '+select_columns(connection)+',properties_json FROM domains'+clause+
                                       ' ORDER BY score DESC,length,rank',params):
            yield dict(row)
