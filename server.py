#!/usr/bin/env python3
import http.server
import os
import re
import socketserver
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = "https://overpass-api.de/api/interpreter"
CACHE = {}
CACHE_BYTES = 0
CACHE_TTL = 86400
CACHE_MAX_BYTES = 10 * 1024 * 1024
RETRYABLE = (429, 502, 503, 504)


def _normalize_key(payload):
    def round_coord(m):
        lat = "%.2f" % float(m.group(2))
        lon = "%.2f" % float(m.group(4))
        return m.group(1) + lat.encode() + m.group(3) + lon.encode() + m.group(5)

    return re.sub(
        rb"(around:\d+,)(-?\d+\.\d+)(,)(-?\d+\.\d+)(\))",
        round_coord,
        payload,
    )


def _prune_cache():
    global CACHE_BYTES
    now = time.time()
    for k, v in list(CACHE.items()):
        if v["time"] + CACHE_TTL < now:
            CACHE_BYTES -= v["size"]
            del CACHE[k]
    while CACHE and CACHE_BYTES > CACHE_MAX_BYTES:
        k = min(CACHE, key=lambda k: CACHE[k]["time"])
        CACHE_BYTES -= CACHE[k]["size"]
        del CACHE[k]


def _stale_fallback():
    if CACHE:
        return CACHE[max(CACHE, key=lambda k: CACHE[k]["time"])]
    return None


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/plain; charset=utf-8", extra=None):
        t0 = getattr(self, "_t0", None)
        if t0 is not None:
            print(
                f"[{time.strftime('%H:%M:%S')}] {self.command} {self.path} -> {code} {time.time() - t0:.2f}s",
                flush=True,
            )
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        self._t0 = time.time()
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                with open("index.html", "rb") as f:
                    body = f.read()
            except OSError:
                self._send(500, b"index.html not found")
                return
            self._send(200, body, "text/html; charset=utf-8")
        elif path.startswith("/sounds/"):
            name = path[len("/sounds/"):]
            if not name or not name.endswith(".ogg") or "/" in name or ".." in name:
                self._send(404, b"not found")
                return
            try:
                with open(os.path.join("sounds", name), "rb") as f:
                    body = f.read()
            except OSError:
                self._send(404, b"not found")
                return
            self._send(200, body, "audio/ogg")
        elif path.startswith("/fonts/"):
            name = path[len("/fonts/"):]
            if not name or "/" in name or ".." in name or not name.endswith((".woff", ".woff2")):
                self._send(404, b"not found")
                return
            try:
                with open(os.path.join("fonts", name), "rb") as f:
                    body = f.read()
            except OSError:
                self._send(404, b"not found")
                return
            ctype = "font/woff2" if name.endswith(".woff2") else "font/woff"
            self._send(200, body, ctype)
        else:
            self._send(404, b"not found")

    def do_POST(self):
        global CACHE_BYTES
        self._t0 = time.time()
        if self.path != "/overpass":
            self._send(404, b"not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(length)
        query = urllib.parse.parse_qs(payload).get(b"data", [payload])[0]
        key = _normalize_key(query)
        _prune_cache()
        cached = CACHE.get(key)
        if cached is not None:
            print(f"[{time.strftime('%H:%M:%S')}] POST /overpass cache hit", flush=True)
            self._send(200, cached["data"], cached["ctype"])
            return
        req = urllib.request.Request(
            ENDPOINT,
            data=payload,
            headers={
                "User-Agent": "rwr/1.0 local proxy",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        for attempt in range(2):
            t1 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=40) as resp:
                    data = resp.read()
                    ctype = resp.headers.get("Content-Type", "application/json")
                print(
                    f"[{time.strftime('%H:%M:%S')}] POST /overpass upstream -> 200 {time.time() - t1:.2f}s ({len(data)} bytes)",
                    flush=True,
                )
                CACHE_BYTES += len(data)
                CACHE[key] = {"data": data, "ctype": ctype, "time": time.time(), "size": len(data)}
                self._send(200, data, ctype)
                return
            except urllib.error.HTTPError as e:
                body = e.read() or str(e.code).encode()
                dt = time.time() - t1
                if e.code in RETRYABLE and attempt == 0:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] POST /overpass upstream -> {e.code} {dt:.2f}s retrying",
                        flush=True,
                    )
                    time.sleep(2)
                    continue
                print(
                    f"[{time.strftime('%H:%M:%S')}] POST /overpass upstream -> {e.code} {dt:.2f}s",
                    flush=True,
                )
                stale = _stale_fallback()
                if stale is not None:
                    self._send(200, stale["data"], stale["ctype"], {"X-RWR-Stale": "1"})
                else:
                    self._send(e.code, body, "text/plain")
                return
            except OSError as e:
                dt = time.time() - t1
                print(
                    f"[{time.strftime('%H:%M:%S')}] POST /overpass upstream -> ERR {dt:.2f}s {e}",
                    flush=True,
                )
                stale = _stale_fallback()
                if stale is not None:
                    self._send(200, stale["data"], stale["ctype"], {"X-RWR-Stale": "1"})
                else:
                    self._send(502, str(e).encode())
                return

    def log_message(self, *args):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    with socketserver.ThreadingTCPServer((host, port), Handler) as srv:
        srv.allow_reuse_address = True
        print(f"RWR serving on http://{host}:{port}  (index.html)")
        print(
            "For Firefox mobile (needs HTTPS): cloudflared tunnel --url http://localhost:"
            + str(port)
        )
        srv.serve_forever()


if __name__ == "__main__":
    main()
