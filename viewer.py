"""Read-only local web viewer for the existing SQLite catalog."""

import csv
import io
import json
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import catalog


ASSETS = Path(__file__).with_name("web")
ROUTES = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/style.css": ("style.css", "text/css"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}


def snapshot(database):
    with closing(catalog.open_catalog(database)) as connection:
        connection.row_factory = sqlite3.Row
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        # ponytail: read the retained shortlist as one snapshot; paginate server-side
        # if catalogs routinely grow beyond tens of thousands of retained names.
        rows = [dict(row) for row in connection.execute(
            "SELECT " + ", ".join(catalog.COLUMNS) + " FROM domains ORDER BY rank"
        )]
    return {"rows": rows, "metadata": {key: metadata.get(key) for key in
            ("created_at", "examined", "row_count", "cap_reached", "ranking_version")}}


def make_server(database, port=8765):
    database = Path(database).resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in allowed_hosts:
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            try:
                if path == "/export.csv":
                    params = parse_qs(urlsplit(self.path).query)
                    query = params.get("search", [""])[0].strip().lower()
                    pattern = params.get("pattern", [""])[0]
                    length = params.get("length", [""])[0]
                    state = params.get("state", [""])[0]
                    rows = [row for row in snapshot(database)["rows"]
                            if (not query or query in row["domain"])
                            and (not pattern or pattern in row["reasons"].split(";"))
                            and (not length or str(row["length"]) == length)
                            and (not state or row["availability"] == state)]
                    output = io.StringIO(newline="")
                    writer = csv.DictWriter(output, fieldnames=catalog.COLUMNS)
                    writer.writeheader()
                    writer.writerows(rows)
                    self.respond(200, "text/csv", output.getvalue().encode(), download=True)
                    return
                if path == "/api/catalog":
                    body = json.dumps(snapshot(database)).encode()
                    content_type = "application/json"
                elif path in ROUTES:
                    filename, content_type = ROUTES[path]
                    body = (ASSETS / filename).read_bytes()
                else:
                    self.send_error(404)
                    return
            except (OSError, ValueError, sqlite3.Error):
                body = json.dumps({"error": "The catalog could not be read. Check the database, then refresh."}).encode()
                self.respond(503, "application/json", body)
                return
            self.respond(200, content_type, body)

        def respond(self, status, content_type, body, download=False):
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if download:
                self.send_header("Content-Disposition", 'attachment; filename="xyz-shortlist.csv"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(database, port):
    with make_server(database, port) as server:
        print(f"Catalog viewer: http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()
