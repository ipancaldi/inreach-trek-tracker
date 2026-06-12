#!/usr/bin/env python3
"""
inReach Trek Tracker — local server.

Serves the web app, proxies the Garmin MapShare KML feed
(share.garmin.com blocks cross-origin browser requests), and bridges
the browser to the physical device: scans the Mac's USB bus for a
Garmin, finds its mounted volume, and serves GPX track logs from it.

Run:  python3 server.py        then open http://localhost:8765
"""
import base64
import json
import os
import subprocess
import urllib.parse
import urllib.request
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = 8765
GARMIN_FEED = "https://share.garmin.com/Feed/Share/"
LIVETRACK_PROFILE = "https://live.garmin.com/"
LIVETRACK_SESSION = "https://livetrack.garmin.com/session/{id}/token/{token}"
GARMIN_USB_VENDOR_ID = "0x091e"
MAX_GPX_FILES = 200


_usb_cache = {"t": 0.0, "v": None}
_usb_lock = threading.Lock()


def find_garmin_usb():
    """Return info about a Garmin device on the USB bus, or None.
    system_profiler takes seconds, so cache the result briefly."""
    with _usb_lock:
        if time.time() - _usb_cache["t"] < 10:
            return _usb_cache["v"]
        _usb_cache["v"] = _scan_garmin_usb()
        _usb_cache["t"] = time.time()
        return _usb_cache["v"]


def _scan_garmin_usb():
    try:
        out = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-json"],
            capture_output=True, timeout=20,
        ).stdout
        data = json.loads(out)
    except Exception:
        return None

    def walk(items):
        for it in items or []:
            yield it
            yield from walk(it.get("_items"))

    for it in walk(data.get("SPUSBDataType")):
        blob = " ".join(str(it.get(k, "")) for k in ("_name", "manufacturer")).lower()
        if "garmin" in blob or "inreach" in blob or it.get("vendor_id", "").startswith(GARMIN_USB_VENDOR_ID):
            return {
                "name": it.get("_name"),
                "manufacturer": it.get("manufacturer"),
                "serial": it.get("serial_num"),
            }
    return None


def find_garmin_volumes():
    """Mounted volumes that look like a Garmin device."""
    vols = []
    root = Path("/Volumes")
    if not root.is_dir():
        return vols
    for v in root.iterdir():
        try:
            if not v.is_dir() or v.name == "Macintosh HD":
                continue
            name = v.name.lower()
            if "garmin" in name or "inreach" in name or (v / "Garmin").is_dir():
                vols.append(str(v))
        except OSError:
            continue
    return vols


def read_device_info(volumes):
    """Parse GarminDevice.xml on the volume to identify the device."""
    import xml.etree.ElementTree as ET
    for vol in volumes:
        for sub in ("GARMIN", "Garmin"):
            p = Path(vol) / sub / "GarminDevice.xml"
            if not p.is_file():
                continue
            try:
                root = ET.parse(p).getroot()
                ns = {"g": "http://www.garmin.com/xmlschemas/GarminDevice/v2"}
                model = root.find("g:Model", ns)
                return {
                    "model": model.findtext("g:Description", default="", namespaces=ns) if model is not None else "",
                    "firmware": model.findtext("g:SoftwareVersion", default="", namespaces=ns) if model is not None else "",
                    "unitId": root.findtext("g:Id", default="", namespaces=ns),
                }
            except Exception:
                continue
    return None


def find_newfiles_dir(volumes):
    """The folder the device imports GPX routes/waypoints from."""
    for vol in volumes:
        for sub in ("Garmin/NewFiles", "GARMIN/NewFiles", "GARMIN/NEWFILES"):
            p = Path(vol) / sub
            if p.is_dir():
                return p
    return None


def find_gpx_files(volumes):
    """GPX files on the device volumes (Garmin keeps them in Garmin/GPX)."""
    files = []
    for vol in volumes:
        vol_path = Path(vol)
        # Likely locations first, then a bounded full scan
        candidates = [vol_path / "Garmin" / "GPX", vol_path / "GPX", vol_path]
        seen = set()
        for base in candidates:
            if not base.is_dir():
                continue
            try:
                for f in sorted(base.rglob("*.gpx")):
                    if f.name.startswith("._"):  # macOS AppleDouble junk
                        continue
                    real = os.path.realpath(f)
                    if real in seen:
                        continue
                    seen.add(real)
                    st = f.stat()
                    files.append({
                        "path": str(f),
                        "name": f.name,
                        "size": st.st_size,
                        "modified": int(st.st_mtime),
                    })
                    if len(files) >= MAX_GPX_FILES:
                        return files
            except OSError:
                continue
    files.sort(key=lambda x: -x["modified"])
    return files


def fetch_rsc(url):
    """Fetch a live.garmin.com / livetrack.garmin.com page as a Next.js RSC
    flight stream — the JSON session data appears unescaped in the body."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh) inReach-Trek-Tracker/1.0",
        "RSC": "1",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_json_value(text, start):
    """Brace/bracket-match a JSON value starting at text[start], string-aware."""
    open_ch = text[start]
    close_ch = {"{": "}", "[": "]"}[open_ch]
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("Unbalanced JSON")


def livetrack_sessions_api(name):
    """Sessions via Garmin's REST API — the only source that always lists an
    in-progress walk. Auth: CSRF token + cookies from the profile page."""
    import http.cookiejar
    import re
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    ua = "Mozilla/5.0 (Macintosh) inReach-Trek-Tracker/1.0"
    req = urllib.request.Request(
        LIVETRACK_PROFILE + urllib.parse.quote(name), headers={"User-Agent": ua})
    with opener.open(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    m_tok = re.search(r'name="csrf-token" content="([^"]+)"', html)
    m_guid = re.search(r'garminGuid\\?":\\?"([0-9a-f-]{36})', html)
    if not (m_tok and m_guid):
        return None
    req2 = urllib.request.Request(
        f"https://live.garmin.com/api/user/{m_guid.group(1)}/profile-sessions?limit=20",
        headers={
            "User-Agent": ua,
            "Livetrack-Csrf-Token": m_tok.group(1),
            "Accept": "application/json",
        })
    with opener.open(req2, timeout=30) as resp:
        return json.loads(resp.read())


def livetrack_sessions(name):
    """Active + completed LiveTrack sessions for a live.garmin.com profile."""
    try:
        api = livetrack_sessions_api(name)
        if api and ("activeSessions" in api or "completedSessions" in api):
            return api
    except Exception:
        pass  # fall through to the page-payload scrape
    body = fetch_rsc(LIVETRACK_PROFILE + urllib.parse.quote(name))
    # several "garminGuid" objects exist (UI component props); we want the
    # data object — the one that carries the session lists
    found = None
    pos = 0
    while True:
        i = body.find('{"garminGuid"', pos)
        if i < 0:
            break
        try:
            obj = extract_json_value(body, i)
            guid = obj.get("garminGuid")
            if isinstance(guid, str) and guid != "$undefined":
                if "activeSessions" in obj or "completedSessions" in obj:
                    return obj
                if found is None:
                    found = obj  # profile exists even if no session data yet
        except ValueError:
            pass
        pos = i + 1
    return found


def livetrack_points(session_id, token):
    """All track points of a LiveTrack session, normalized."""
    body = fetch_rsc(LIVETRACK_SESSION.format(
        id=urllib.parse.quote(session_id), token=urllib.parse.quote(token)))
    points = []
    pos = 0
    while True:
        i = body.find('"trackPoints":', pos)
        if i < 0:
            break
        arr_start = body.index("[", i)
        try:
            for tp in extract_json_value(body, arr_start):
                p = tp.get("position") or {}
                if "lat" not in p:
                    continue
                points.append({
                    "lat": p["lat"],
                    "lon": p["lon"],
                    "ele": tp.get("altitude"),
                    "time": tp.get("dateTime"),
                    "speed": tp.get("speed"),
                    "course": tp.get("course"),
                })
        except ValueError:
            pass
        pos = arr_start + 1
    # dedupe (pages can overlap) and sort by time
    seen = set()
    unique = []
    for p in points:
        key = (p["time"], p["lat"], p["lon"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    unique.sort(key=lambda p: p["time"] or "")
    return unique


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/mapshare":
            self.handle_mapshare()
        elif path == "/api/livetrack/sessions":
            self.handle_livetrack_sessions()
        elif path == "/api/livetrack/track":
            self.handle_livetrack_track()
        elif path == "/api/device/status":
            self.handle_device_status()
        elif path == "/api/device/gpx":
            self.handle_device_gpx()
        else:
            super().do_GET()

    # ----- device bridge -----

    def handle_device_status(self):
        volumes = find_garmin_volumes()
        # The volume + GarminDevice.xml is the authoritative signal; the USB
        # bus scan (slow, ~3 s) is only a fallback when nothing is mounted.
        usb = None if volumes else find_garmin_usb()
        gpx = find_gpx_files(volumes)
        self.send_json(200, {
            "usb": usb,
            "device": read_device_info(volumes),
            "volumes": volumes,
            "gpxFiles": gpx,
            "canReceive": find_newfiles_dir(volumes) is not None,
            "connected": bool(usb or volumes),
        })

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path == "/api/device/send":
            self.handle_device_send()
        else:
            self.send_json(404, {"error": "Not found"})

    def handle_device_send(self):
        """Write a GPX route/waypoints into Garmin/NewFiles for the device to import."""
        target = find_newfiles_dir(find_garmin_volumes())
        if target is None:
            self.send_json(404, {"error": "No device connected (Garmin/NewFiles not found)"})
            return
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length <= 10_000_000:
            self.send_json(400, {"error": "Empty or oversized payload"})
            return
        body = self.rfile.read(length)
        if b"<gpx" not in body[:1000]:
            self.send_json(400, {"error": "Payload is not GPX"})
            return
        import time
        fname = "trek-" + time.strftime("%Y%m%d-%H%M%S") + ".gpx"
        try:
            with open(target / fname, "wb") as f:
                f.write(body)
        except OSError as e:
            self.send_json(500, {"error": f"Write failed: {e}"})
            return
        self.send_json(200, {"ok": True, "file": str(target / fname)})

    def handle_device_gpx(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        raw = qs.get("path", [""])[0]
        real = os.path.realpath(raw)
        # Only serve .gpx files that live on a mounted volume
        if not real.lower().endswith(".gpx") or not real.startswith("/Volumes/"):
            self.send_json(400, {"error": "Invalid path"})
            return
        try:
            with open(real, "rb") as f:
                body = f.read()
        except OSError as e:
            self.send_json(404, {"error": f"Cannot read file: {e}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/gpx+xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ----- LiveTrack (live.garmin.com) -----

    def handle_livetrack_sessions(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = qs.get("name", [""])[0].strip()
        if not name:
            self.send_json(400, {"error": "Missing profile name"})
            return
        try:
            data = livetrack_sessions(name)
        except Exception as e:
            self.send_json(502, {"error": f"Could not reach Garmin: {e}"})
            return
        if data is None:
            self.send_json(404, {"error": f"No Garmin Share profile found for '{name}'"})
            return
        self.send_json(200, data)

    def handle_livetrack_track(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        sid = qs.get("id", [""])[0].strip()
        token = qs.get("token", [""])[0].strip()
        if not sid or not token:
            self.send_json(400, {"error": "Missing session id or token"})
            return
        try:
            pts = livetrack_points(sid, token)
        except Exception as e:
            self.send_json(502, {"error": f"Could not reach Garmin: {e}"})
            return
        self.send_json(200, {"points": pts})

    # ----- MapShare proxy -----

    def handle_mapshare(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        name = params.get("name", [""])[0].strip()
        password = params.get("password", [""])[0]
        d1 = params.get("d1", [""])[0]
        d2 = params.get("d2", [""])[0]

        if not name:
            self.send_json(400, {"error": "Missing MapShare name"})
            return

        url = GARMIN_FEED + urllib.parse.quote(name)
        feed_params = {}
        if d1:
            feed_params["d1"] = d1
        if d2:
            feed_params["d2"] = d2
        if feed_params:
            url += "?" + urllib.parse.urlencode(feed_params)

        req = urllib.request.Request(url, headers={"User-Agent": "inReach-Trek-Tracker/1.0"})
        if password:
            # Password-protected MapShare uses Basic auth with an empty username
            token = base64.b64encode(f":{password}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.google-earth.kml+xml")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self.send_json(401, {"error": "MapShare is password-protected or the password is wrong"})
            elif e.code == 404:
                self.send_json(404, {"error": f"No MapShare page found for '{name}'"})
            else:
                self.send_json(502, {"error": f"Garmin returned HTTP {e.code}"})
        except Exception as e:
            self.send_json(502, {"error": f"Could not reach Garmin: {e}"})

    # ----- helpers -----

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # keep the console quiet


if __name__ == "__main__":
    print(f"inReach Trek Tracker → http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
