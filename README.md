# inReach Trek Tracker

A minimal web app for tracking walks with a **Garmin inReach Mini 2** on an
open-source map (Leaflet + OpenStreetMap / OpenTopoMap).

## Run

```sh
python3 server.py
# open http://localhost:8765
```

No dependencies — Python stdlib only.

To use it from your phone on a walk, open `http://<your-computer's-IP>:8765`
while both devices are on the same network.

### GitHub Pages demo

The static app also runs on GitHub Pages: the map, pins & trek builder,
GPX import/export, stats, and my-position all work in the browser alone.
**Live tracking and USB device connection need the local server** (Garmin
blocks cross-origin requests and USB needs a machine the device plugs
into), so those features show a hint instead when the server is absent.

## How it connects to the device

The Mini 2 has no public local API (its Bluetooth protocol is private to
Garmin's phone apps), so the app taps the device's strongest capability
instead: **Iridium satellite tracking**.

| Path | What it gives you |
|------|-------------------|
| **Connect device (USB)** | The Mini 2 mounts as a USB drive on the Mac. The **Connect device** button scans for it via the local server, identifies it from `GarminDevice.xml` (model, firmware, unit ID), lists the GPX tracks stored on it, and loads them onto the map with one click. After the first scan the app keeps watching, so plug/unplug is detected automatically. |
| **Send trek to device** | Writes your pinned trek as a GPX into `Garmin/NewFiles` on the device — the folder the Mini 2 imports from. Restart/sync the device and the route appears on it, ready to navigate. |
| **LiveTrack (live.garmin.com)** | The primary live feed. The device sends track points over the Iridium satellite network; Garmin publishes them as LiveTrack sessions on `live.garmin.com/<your-share-name>`. The local server reads the portal's data stream and serves `/api/livetrack/sessions` (active + past walks) and `/api/livetrack/track` (the points: position, elevation, speed, time). An active walk auto-loads with a LIVE badge and is re-fetched every 60 s. |
| **MapShare KML feed (fallback)** | Older accounts expose `share.garmin.com/Feed/Share/<name>` KML; the app falls back to it automatically if no Garmin Share profile is found. |
| **GPX import / export** | Import any GPX (Garmin Explore exports, other apps) by file picker or drag & drop; export your pins + loaded track as GPX. |

## One-time device setup for live tracking

1. Go to [explore.garmin.com](https://explore.garmin.com) → **Social → MapShare** and turn MapShare **on**. Note your share name (`share.garmin.com/<name>`) and optional password.
2. On the Mini 2, start **Tracking** when you begin a walk. Points flow: device → Iridium → Garmin → this app.
3. Enter your MapShare name in the app, tick **Auto-refresh**, and walk.

Tip: the default send interval is 10 min; lower it (Settings → Tracking →
Send Interval) for a denser live line. The on-device *log* interval is much
finer — that detail comes in via GPX import afterwards.

## Features

- Live satellite track with per-fix popups (time, elevation, speed, course, event, messages, SOS flag)
- Trek stats: distance, duration, average speed, fix count
- Pin mode: click to drop numbered pins that auto-connect into a trek line with per-leg and total distance; drag to adjust
- GPX import (tracks, routes, waypoints) and export (your pins + the loaded track)
- OpenStreetMap and OpenTopoMap (contour lines for hiking) base layers
- Browser geolocation ("My position") as a quick local reference
