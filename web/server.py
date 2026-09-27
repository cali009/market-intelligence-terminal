"""
Dual-Market Intelligence Web Dashboard Server
Serves static edge assets and JSON endpoints for Cloudflare Pages / Preview.
Binds to 0.0.0.0:8000 for preview proxy compatibility.
"""

import sys
import os
import json
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import urllib.parse

BASE_DIR = Path(__file__).resolve().parent.parent
FEEDS_DIR = BASE_DIR / "data" / "feeds"
DIST_DIR = BASE_DIR / "data" / "dist"

def get_data_dir() -> Path:
    if FEEDS_DIR.exists() and (FEEDS_DIR / "daily_summary.json").exists():
        return FEEDS_DIR
    return DIST_DIR
WEB_DIR = BASE_DIR / "web"


class MarketIntelHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self):
        # Enable CORS and anti-caching for real-time edge updates
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        data_dir = get_data_dir()

        # Handle robots.txt and sitemap.xml with strict plain/xml MIME types
        if path == "/robots.txt":
            robots_file = WEB_DIR / "robots.txt"
            if robots_file.exists():
                with open(robots_file, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
        elif path == "/sitemap.xml":
            sitemap_file = WEB_DIR / "sitemap.xml"
            if sitemap_file.exists():
                with open(sitemap_file, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        # Route API requests to data/feeds or data/dist
        if path.startswith("/api/"):
            clean_sub = path[len("/api/"):].lstrip("/")
            if clean_sub.startswith("symbols/") or clean_sub.startswith("symbol/"):
                clean = clean_sub.replace("symbols/", "").replace("symbol/", "").replace(".json", "")
                sym = clean.strip().upper()
                sym_file = data_dir / "symbols" / f"{sym}.json"
                return self._serve_json_file(sym_file)

            alias_map = {
                "summary": "daily_summary.json",
                "summary.json": "daily_summary.json",
                "daily_summary": "daily_summary.json",
                "daily_summary.json": "daily_summary.json",
                "catalysts": "news_filings.json",
                "catalysts.json": "news_filings.json",
            }
            if clean_sub in alias_map:
                return self._serve_json_file(data_dir / alias_map[clean_sub])

            target_filename = clean_sub if clean_sub.endswith(".json") else f"{clean_sub}.json"
            candidate_file = data_dir / target_filename
            if candidate_file.exists():
                return self._serve_json_file(candidate_file)
            elif (data_dir / clean_sub).exists():
                return self._serve_json_file(data_dir / clean_sub)

        # Default to static file serving
        return super().do_GET()

    def _serve_json_file(self, file_path: Path):
        if not file_path.exists():
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Resource not found: {file_path.name}"}).encode("utf-8"))
            return

        try:
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))


def run_server(port: int = 8000):
    server_address = ("0.0.0.0", port)
    httpd = HTTPServer(server_address, MarketIntelHandler)
    print(f"Market Intelligence Web Server listening on http://0.0.0.0:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        httpd.server_close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    run_server(port)
