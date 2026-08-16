#!/usr/bin/env python3
import http.server
import json
import math
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

OSAPI = "https://opensky-network.org/api/states/all"
OSAPI_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/"
    "opensky-network/protocol/openid-connect/token"
)
OSAPI_USER_AGENT = "https://github.com/ways/rwr"
OSAPI_CACHE = {}
OSAPI_CACHE_BYTES = 0
OSAPI_CACHE_MAX_BYTES = 2 * 1024 * 1024
OSAPI_CACHE_MAX_AGE = 3600
RATE_LIMIT = {}
RATE_WINDOW = 60.0
try:
    RATE_MAX = int(os.environ.get("RWR_RATE_MAX", 60))
except ValueError:
    RATE_MAX = 60
OSAPI_CLIENT_ID = os.environ.get("OPENSKY_CLIENT_ID", "").strip()
OSAPI_CLIENT_SECRET = os.environ.get("OPENSKY_CLIENT_SECRET", "").strip()
OSAPI_CREDENTIALS = bool(OSAPI_CLIENT_ID and OSAPI_CLIENT_SECRET)
_DEFAULT_OSAPI_TTL = 10 if OSAPI_CREDENTIALS else 240
try:
    OSAPI_TTL = int(os.environ.get("RWR_OSAPI_TTL", _DEFAULT_OSAPI_TTL))
except ValueError:
    OSAPI_TTL = _DEFAULT_OSAPI_TTL
OSAPI_REMAINING = None
OSAPI_BACKOFF_UNTIL = 0.0


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


def _prune_osapi_cache():
    global OSAPI_CACHE_BYTES
    now = time.time()
    for k, v in list(OSAPI_CACHE.items()):
        if v["ts"] + OSAPI_CACHE_MAX_AGE < now:
            OSAPI_CACHE_BYTES -= v["size"]
            del OSAPI_CACHE[k]
    while OSAPI_CACHE and OSAPI_CACHE_BYTES > OSAPI_CACHE_MAX_BYTES:
        k = min(OSAPI_CACHE, key=lambda k: OSAPI_CACHE[k]["ts"])
        OSAPI_CACHE_BYTES -= OSAPI_CACHE[k]["size"]
        del OSAPI_CACHE[k]


def _effective_ttl():
    """Cache TTL, stretched to stretch the daily credit budget when it runs low."""
    ttl = OSAPI_TTL
    if OSAPI_REMAINING is not None and OSAPI_REMAINING < 100:
        pace = 3600 * 12 / max(1, OSAPI_REMAINING)
        if pace > ttl:
            ttl = int(pace)
    return ttl


def _rate_allowed(ip):
    now = time.time()
    if len(RATE_LIMIT) > 10000:
        for k, (t, _) in list(RATE_LIMIT.items()):
            if t + RATE_WINDOW < now:
                del RATE_LIMIT[k]
    bucket = RATE_LIMIT.get(ip)
    if bucket is None or bucket[0] + RATE_WINDOW < now:
        bucket = [now, 0]
        RATE_LIMIT[ip] = bucket
    bucket[1] += 1
    return bucket[1] <= RATE_MAX


class _TokenManager:
    def __init__(self):
        self.token = None
        self.expires_at = 0

    def get(self):
        if self.token and time.time() < self.expires_at:
            return self.token
        self.token = None
        if not OSAPI_CREDENTIALS:
            return None
        data = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": OSAPI_CLIENT_ID,
                "client_secret": OSAPI_CLIENT_SECRET,
            }
        ).encode()
        req = urllib.request.Request(OSAPI_TOKEN_URL, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                j = json.loads(resp.read().decode("utf-8", "replace"))
            self.token = j.get("access_token")
            self.expires_at = time.time() + int(j.get("expires_in", 1800)) - 30
        except (OSError, ValueError):
            self.token = None
        return self.token


TOKENS = _TokenManager()


def _osapi_bbox(lat, lon, rng):
    dlat = rng / 111320.0
    dlon = rng / (111320.0 * math.cos(math.radians(lat)))
    return {
        "lamin": max(-90.0, lat - dlat),
        "lamax": min(90.0, lat + dlat),
        "lomin": max(-180.0, lon - dlon),
        "lomax": min(180.0, lon + dlon),
    }


def _haversine(a_lat, a_lon, b_lat, b_lon):
    d_lat = math.radians(b_lat - a_lat)
    d_lon = math.radians(b_lon - a_lon)
    s = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(a_lat))
        * math.cos(math.radians(b_lat))
        * math.sin(d_lon / 2) ** 2
    )
    return 2 * 6371000.0 * math.asin(math.sqrt(s))


def _bearing(a_lat, a_lon, b_lat, b_lon):
    p1 = math.radians(a_lat)
    p2 = math.radians(b_lat)
    d_lon = math.radians(b_lon - a_lon)
    y = math.sin(d_lon) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(d_lon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


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
        elif path == "/osapi":
            self._osapi()
        else:
            self._send(404, b"not found")

    def _osapi(self):
        global OSAPI_CACHE_BYTES, OSAPI_REMAINING, OSAPI_BACKOFF_UNTIL
        if not _rate_allowed(self.client_address[0]):
            self._send(429, b"rate limited")
            return
        q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
        try:
            lat = float(q["lat"][0])
            lon = float(q["lon"][0])
            rng = float(q["range"][0])
        except (KeyError, ValueError, IndexError):
            self._send(400, b"lat, lon, range required")
            return
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            self._send(400, b"bad coordinates")
            return
        if rng not in (10000, 50000):
            self._send(400, b"range must be 10000 or 50000")
            return
        now = time.time()
        key = "%.2f,%.2f,%.0f" % (lat, lon, rng)
        cached = OSAPI_CACHE.get(key)
        if OSAPI_BACKOFF_UNTIL > now:
            if cached is not None:
                self._send(200, cached["data"], "application/json", {"X-RWR-Stale": "1"})
            else:
                self._send(503, b"upstream rate limited", "text/plain")
            return
        if cached is not None and cached["ts"] + _effective_ttl() >= now:
            print(f"[{time.strftime('%H:%M:%S')}] GET /osapi cache hit", flush=True)
            self._send(200, cached["data"], "application/json")
            return
        bbox = _osapi_bbox(lat, lon, rng)
        url = OSAPI + "?" + urllib.parse.urlencode(
            {k: "%.4f" % v for k, v in bbox.items()}
        )
        headers = {"User-Agent": OSAPI_USER_AGENT}
        token = TOKENS.get()
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(url, headers=headers)
        data = None
        t1 = time.time()
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                    rem = resp.headers.get("X-Rate-Limit-Remaining")
                    if rem is not None:
                        OSAPI_REMAINING = int(rem)
                OSAPI_BACKOFF_UNTIL = 0.0
                break
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    TOKENS.token = None
                if e.code == 429:
                    retry = e.headers.get("X-Rate-Limit-Retry-After-Seconds")
                    if retry is not None:
                        OSAPI_BACKOFF_UNTIL = time.time() + int(retry)
                if e.code in RETRYABLE and attempt == 0:
                    time.sleep(2)
                    continue
                dt = time.time() - t1
                print(
                    f"[{time.strftime('%H:%M:%S')}] GET /osapi upstream -> {e.code} {dt:.2f}s",
                    flush=True,
                )
                stale = OSAPI_CACHE.get(key)
                if stale is not None:
                    self._send(200, stale["data"], "application/json", {"X-RWR-Stale": "1"})
                else:
                    self._send(e.code, str(e.code).encode(), "text/plain")
                return
            except OSError as e:
                dt = time.time() - t1
                print(
                    f"[{time.strftime('%H:%M:%S')}] GET /osapi upstream -> ERR {dt:.2f}s {e}",
                    flush=True,
                )
                stale = OSAPI_CACHE.get(key)
                if stale is not None:
                    self._send(200, stale["data"], "application/json", {"X-RWR-Stale": "1"})
                else:
                    self._send(502, str(e).encode(), "text/plain")
                return
        states = []
        for row in data.get("states") or []:
            if len(row) < 14:
                continue
            a_lon, a_lat = row[5], row[6]
            if a_lat is None or a_lon is None:
                continue
            dist = _haversine(lat, lon, a_lat, a_lon)
            if dist > rng:
                continue
            alt = row[7]
            if alt is None:
                alt = row[13]
            states.append(
                {
                    "id": row[0],
                    "cs": (row[1] or "").strip(),
                    "country": row[2],
                    "lat": a_lat,
                    "lon": a_lon,
                    "alt": alt,
                    "v": row[9],
                    "t": row[10],
                    "vr": row[11],
                    "og": bool(row[8]),
                    "tp": row[3],
                    "lc": row[4],
                    "cat": row[17] if len(row) > 17 else None,
                    "d": round(dist, 1),
                    "b": round(_bearing(lat, lon, a_lat, a_lon), 1),
                }
            )
        states.sort(key=lambda s: s["d"])
        body = json.dumps({"time": data.get("time"), "states": states}).encode("utf-8")
        _prune_osapi_cache()
        OSAPI_CACHE_BYTES += len(body)
        OSAPI_CACHE[key] = {"data": body, "ts": now, "size": len(body)}
        print(
            f"[{time.strftime('%H:%M:%S')}] GET /osapi upstream -> 200 {time.time() - t1:.2f}s ({len(states)} states)"
            + (f" credits={OSAPI_REMAINING}" if OSAPI_REMAINING is not None else ""),
            flush=True,
        )
        self._send(200, body, "application/json")

    def do_POST(self):
        global CACHE_BYTES
        self._t0 = time.time()
        if self.path != "/overpass":
            self._send(404, b"not found")
            return
        if not _rate_allowed(self.client_address[0]):
            self._send(429, b"rate limited")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 1_000_000:
            self._send(413, b"payload too large")
            return
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
