#!/usr/bin/env python3
"""Small local web server for the departures UI."""

from __future__ import annotations

import json
import mimetypes
import os
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from train_times_web import get_autocomplete_suggestions, get_oebb_rows, get_wl_rows


ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.respond_json(HTTPStatus.OK, {"ok": True})
            return

        if parsed.path == "/api/autocomplete":
            params = parse_qs(parsed.query)
            provider = params.get("provider", ["oebb"])[0]
            query = params.get("query", [""])[0]
            suggestions = get_autocomplete_suggestions(provider, query)
            self.respond_json(HTTPStatus.OK, {"suggestions": suggestions})
            return

        if parsed.path == "/":
            self.path = "/index.html"

        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/search":
            self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length).decode("utf-8")
            body = json.loads(raw_body or "{}")

            provider = "wl" if body.get("provider") == "wl" else "oebb"
            target = str(body.get("target", "")).strip()
            limit = max(1, min(12, int(body.get("limit", 5))))

            if not target:
                self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "Please enter a station name or stopId."})
                return

            if provider == "oebb":
                rows = get_oebb_rows(target, limit)
            else:
                rows = get_wl_rows(int(target), limit)

            self.respond_json(
                HTTPStatus.OK,
                {
                    "provider": provider,
                    "target": target,
                    "limit": limit,
                    "rows": [row.as_dict() for row in rows],
                },
            )
        except ValueError:
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "For Wiener Linien, please enter a numeric stopId."})
        except Exception as exc:  # noqa: BLE001
            self.respond_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def guess_type(self, path: str) -> str:
        guessed, _ = mimetypes.guess_type(path)
        return guessed or "application/octet-stream"

    def respond_json(self, status: HTTPStatus, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Server running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
