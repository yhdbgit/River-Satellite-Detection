#!/usr/bin/env python3
"""Serve the local candidate-selection dashboard and persist five sites."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SELECTION_PATH = WORKSPACE_ROOT / "config" / "selected_sites.json"


class DashboardHandler(SimpleHTTPRequestHandler):
    server_version = "ICTCBDashboard/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WORKSPACE_ROOT), **kwargs)

    def send_json(self, payload: Dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/dashboard/")
            self.end_headers()
            return
        if path == "/api/selections":
            if not SELECTION_PATH.exists():
                self.send_json({"selected_ids": [], "sites": []})
                return
            try:
                payload = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.send_json(
                    {"error": "Saved selection is unreadable."},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self.send_json(payload)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/selections":
            self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json({"error": "Invalid content length."}, HTTPStatus.BAD_REQUEST)
            return
        if content_length <= 0 or content_length > 64 * 1024:
            self.send_json({"error": "Invalid request size."}, HTTPStatus.BAD_REQUEST)
            return

        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json({"error": "Invalid JSON."}, HTTPStatus.BAD_REQUEST)
            return

        selected_ids = payload.get("selected_ids")
        sites = payload.get("sites")
        if (
            not isinstance(selected_ids, list)
            or not isinstance(sites, list)
            or len(selected_ids) != 5
            or len(sites) != 5
            or len(set(selected_ids)) != 5
        ):
            self.send_json(
                {"error": "Exactly five unique sites are required."},
                HTTPStatus.BAD_REQUEST,
            )
            return

        normalized = {"selected_ids": selected_ids, "sites": sites}
        SELECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix="selected_sites_",
            suffix=".json",
            dir=SELECTION_PATH.parent,
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                json.dump(normalized, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            Path(temporary_name).replace(SELECTION_PATH)
        finally:
            temporary_path = Path(temporary_name)
            if temporary_path.exists():
                temporary_path.unlink()

        self.send_json({"saved": True, "path": str(SELECTION_PATH)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local validation dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard: http://{args.host}:{args.port}/dashboard/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
