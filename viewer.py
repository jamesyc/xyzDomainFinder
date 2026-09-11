"""Read-only, paginated local web viewer for the scored SQLite catalog."""

import csv
import io
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import chain
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import catalog
import scoring

ASSETS=Path(__file__).with_name('web')
ROUTES={'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),
        '/style.css':('style.css','text/css'),'/favicon.svg':('favicon.svg','image/svg+xml')}


def query_options(query):
    params=parse_qs(query)
    allowed={'search','pattern','length','state','min_score','page','page_size','domain'}
    if set(params)-allowed: raise ValueError('Unknown query parameter')
    result={}
    for key,values in params.items():
        if len(values)!=1: raise ValueError('Only one value per filter is supported')
        value=values[0]
        if key in ('page','page_size','min_score','length'):
            value=int(value)
            if key == 'length' and value not in (6,7,8,9): raise ValueError('Length must be 6–9')
            if key in ('page','page_size') and value < 1: raise ValueError('Page and page size must be positive')
            if key == 'page_size' and value > 100: raise ValueError('Page size must not exceed 100')
            if key == 'min_score' and value < 0: raise ValueError('Minimum score must be nonnegative')
        elif key == 'pattern' and value not in scoring.RULES: raise ValueError('Unknown property')
        elif key == 'state' and value not in ('unchecked','available','unavailable','unknown'): raise ValueError('Unknown availability state')
        elif len(value)>100: raise ValueError('Search text is too long')
        result[key]=value
    return result


def snapshot(database,filters=None):
    filters=filters or {}
    page_number=filters.get('page',1);size=filters.get('page_size',20)
    result=catalog.page(database,filters,size,(page_number-1)*size)
    result.update(page=page_number,page_size=size,metadata=catalog.summary(database))
    return result


def make_server(database,port=8765):
    database=Path(database).resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
                self.send_error(403);return
            parsed=urlsplit(self.path)
            try:
                if parsed.path in ROUTES:
                    filename,kind=ROUTES[parsed.path]
                    self.respond(200,kind,(ASSETS/filename).read_bytes());return
                if parsed.path not in ('/api/catalog','/api/domain','/export.csv'):
                    self.send_error(404);return
                try: filters=query_options(parsed.query)
                except (ValueError,OverflowError) as error:
                    self.respond(400,'application/json',json.dumps({'error':str(error)}).encode());return
                if parsed.path == '/export.csv':
                    records=catalog.export_rows(database,filters)
                    try:
                        first=next(records,None)
                        self.headers_for(200,'text/csv',download=True)
                        self.close_connection=True
                        buffer=io.StringIO(newline='')
                        writer=csv.DictWriter(buffer,fieldnames=(*catalog.COLUMNS,'properties_json'))
                        writer.writeheader()
                        self.wfile.write(buffer.getvalue().encode())
                        for row in chain([first] if first is not None else [],records):
                            buffer.seek(0);buffer.truncate(0);writer.writerow(row)
                            self.wfile.write(buffer.getvalue().encode())
                    finally: records.close()
                    return
                if parsed.path == '/api/domain':
                    domain=filters.get('domain','').lower()
                    if not re.fullmatch(r'[0-9]{6,9}\.xyz',domain):
                        self.respond(400,'application/json',b'{"error":"Invalid numeric domain"}');return
                    result=catalog.detail(database,domain)
                    if result is None:
                        self.respond(404,'application/json',b'{"error":"Candidate not found"}');return
                else:
                    result=snapshot(database,filters)
                self.respond(200,'application/json',json.dumps(result).encode())
            except (BrokenPipeError,ConnectionResetError):
                return
            except (OSError,ValueError,sqlite3.Error):
                self.respond(503,'application/json',b'{"error":"The scored catalog could not be read. Rebuild it, then refresh."}')

        def headers_for(self,status,kind,length=None,download=False):
            self.send_response(status)
            self.send_header('Content-Type',kind+'; charset=utf-8')
            if length is not None: self.send_header('Content-Length',str(length))
            if download: self.send_header('Content-Disposition','attachment; filename="xyz-shortlist.csv"')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()

        def respond(self,status,kind,body):
            self.headers_for(status,kind,len(body));self.wfile.write(body)

        def log_message(self,*_): pass

    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def serve(database,port):
    with make_server(database,port) as server:
        print(f'Catalog viewer: http://127.0.0.1:{server.server_port}',flush=True)
        server.serve_forever()
