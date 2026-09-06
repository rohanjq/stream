#!/usr/bin/env python3
"""Download catalog assets during the container build."""
import json
import hashlib
import os
import pathlib
import urllib.request


catalog_path = os.environ.get("MUSIC_CATALOG", "/opt/app/music_catalog.json")
with open(catalog_path, encoding="utf-8") as stream:
    catalog = json.load(stream)

for track in catalog:
    destination = pathlib.Path(track["file"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = track.get("sha256")
    if destination.exists() and destination.stat().st_size > 100_000:
        actual = hashlib.sha256(destination.read_bytes()).hexdigest()
        if expected and actual != expected:
            raise RuntimeError(
                f"checksum mismatch for {track['id']}: expected {expected}, got {actual}")
        continue
    print(f"downloading {track['id']}", flush=True)
    request = urllib.request.Request(
        track["download_url"], headers={"User-Agent": "NightShiftStream/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read()
    if len(data) < 100_000:
        raise RuntimeError(f"downloaded file is unexpectedly small: {track['id']}")
    actual = hashlib.sha256(data).hexdigest()
    if expected and actual != expected:
        raise RuntimeError(
            f"checksum mismatch for {track['id']}: expected {expected}, got {actual}")
    destination.write_bytes(data)
