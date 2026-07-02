#!/usr/bin/env python3
"""Obsługa plików GPX dla TeleM – parsowanie, synchronizacja z wideo."""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
import math


_GPX_EXT_NS = {
    'gpxtpx': 'http://www.garmin.com/xmlschemas/TrackPointExtension/v1',
    'gpxx':   'http://www.garmin.com/xmlschemas/GpxExtensions/v3',
    'power':  'http://www.garmin.com/xmlschemas/PowerExtension/v1',
}


def _parse_extensions(ext_el):
    """Parsuje element <extensions> i zwraca dict z wartościami:
       power, atemp, hr, cad (lub None, gdy brak)."""
    ext = {}
    if ext_el is None:
        return ext

    # Szukamy po lokalnej nazwie taga (pomija namespace)
    for child in ext_el.iter():
        local = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        text = child.text.strip() if child.text else ''
        if local == 'power' and text:
            try:
                ext['power'] = float(text)
            except ValueError:
                pass
        elif local == 'atemp' and text:
            try:
                ext['atemp'] = float(text)
            except ValueError:
                pass
        elif local == 'hr' and text:
            try:
                ext['hr'] = float(text)
            except ValueError:
                pass
        elif local == 'cad' and text:
            try:
                ext['cad'] = float(text)
            except ValueError:
                pass
    return ext


def parse_gpx(gpx_path):
    """Parsuje plik GPX i zwraca listę punktów (datetime, lat, lon, ele, extensions) lub None.
       extensions to dict z kluczami: power, atemp, hr, cad (lub pusty dict)."""
    try:
        tree = ET.parse(gpx_path)
        root = tree.getroot()
    except Exception as exc:
        print(f"[GPX] Błąd parsowania XML: {exc}", flush=True)
        return None

    # Namespace GPX
    ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}

    points = []
    for trkpt in root.iter('{http://www.topografix.com/GPX/1/1}trkpt'):
        lat = float(trkpt.attrib['lat'])
        lon = float(trkpt.attrib['lon'])

        # Wysokość (opcjonalna)
        ele_el = trkpt.find('gpx:ele', ns)
        ele = float(ele_el.text) if ele_el is not None and ele_el.text else 0.0

        # Czas (wymagany)
        time_el = trkpt.find('gpx:time', ns)
        if time_el is None or not time_el.text:
            continue

        try:
            dt = datetime.strptime(time_el.text.strip(), "%Y-%m-%dT%H:%M:%SZ")
            dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            try:
                dt = datetime.strptime(time_el.text.strip(), "%Y-%m-%dT%H:%M:%S.%fZ")
                dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue

        # Filtruj śmieci
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue

        # Parsuj rozszerzenia (power, atemp, hr, cad)
        ext_el = trkpt.find('gpx:extensions', ns)
        ext = _parse_extensions(ext_el)

        points.append((dt, lat, lon, ele, ext))

    if not points:
        print("[GPX] Brak punktów z czasem w pliku GPX.", flush=True)
        return None

    # Sortuj po czasie
    points.sort(key=lambda x: x[0])

    # Deduplikacja czasu
    deduped = []
    for pt in points:
        if not deduped or pt[0] != deduped[-1][0]:
            deduped.append(pt)
        else:
            # scal rozszerzenia przy deduplikacji
            _, _, _, _, ext_new = pt
            _, _, _, _, ext_old = deduped[-1]
            merged = {**ext_old, **ext_new}
            deduped[-1] = (pt[0], pt[1], pt[2], pt[3], merged)

    print(f"[GPX] Wczytano {len(deduped)} punktów z {gpx_path.name}", flush=True)
    # Podsumuj znalezione rozszerzenia
    found = set()
    for _, _, _, _, ext in deduped:
        found.update(ext.keys())
    if found:
        print(f"[GPX] Rozszerzenia: {sorted(found)}", flush=True)
    return deduped


def haversine_m(lat1, lon1, lat2, lon2):
    """Dystans w metrach między dwoma punktami GPS."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def sync_gpx_to_video(points, video_start_dt):
    """
    Synchronizuje punkty GPX z osią czasu wideo.

    Args:
        points: lista (datetime, lat, lon, ele, extensions) – czasy absolutne UTC
        video_start_dt: datetime UTC początku filmu (T=0)

    Returns:
        (speed_samples, track_samples, alt_samples,
         power_samples, atemp_samples, hr_samples, cad_samples) – krotka siedmiu list:
        - speed_samples: [(datetime, speed_kmh), ...]
        - track_samples: [(datetime, distance_m), ...]
        - alt_samples:   [(datetime, altitude_m), ...]
        - power_samples: [(datetime, power_W), ...] lub []
        - atemp_samples: [(datetime, temp_C), ...] lub []
        - hr_samples:    [(datetime, bpm), ...] lub []
        - cad_samples:   [(datetime, rpm), ...] lub []
        lub (None, None, None, None, None, None, None) gdy brak danych.
    """
    if not points:
        return None, None, None, None, None, None, None

    if video_start_dt is None:
        video_start_dt = points[0][0]

    # Ujednolicenie timezone – pracujemy na czasie UTC bez timezone (naive)
    if video_start_dt.tzinfo is not None:
        video_start_dt = video_start_dt.replace(tzinfo=None)
    pts_clean = [(dt.replace(tzinfo=None), lat, lon, ele, ext) for dt, lat, lon, ele, ext in points]

    # --- Próbki prędkości ---
    speed_samples = []
    for i in range(1, len(pts_clean)):
        dt1, lat1, lon1, _, _ = pts_clean[i - 1]
        dt2, lat2, lon2, _, _ = pts_clean[i]
        dt_delta = (dt2 - dt1).total_seconds()
        if dt_delta <= 0:
            continue
        dist_m = haversine_m(lat1, lon1, lat2, lon2)
        speed_ms = dist_m / dt_delta
        speed_kmh = speed_ms * 3.6
        speed_samples.append((dt2, speed_kmh))

    # --- Próbki dystansu (skumulowane) ---
    track_samples = []
    total_m = 0.0
    for i, (dt, lat, lon, _, _) in enumerate(pts_clean):
        if i > 0:
            _, prev_lat, prev_lon, _, _ = pts_clean[i - 1]
            total_m += haversine_m(prev_lat, prev_lon, lat, lon)
        track_samples.append((dt, total_m))

    # --- Próbki wysokości ---
    alt_samples = [(dt, ele) for dt, _, _, ele, _ in pts_clean]

    # --- Rozszerzenia: power, atemp, hr, cad ---
    power_samples = []
    atemp_samples = []
    hr_samples = []
    cad_samples = []
    for dt, _, _, _, ext in pts_clean:
        if 'power' in ext:
            power_samples.append((dt, ext['power']))
        if 'atemp' in ext:
            atemp_samples.append((dt, ext['atemp']))
        if 'hr' in ext:
            hr_samples.append((dt, ext['hr']))
        if 'cad' in ext:
            cad_samples.append((dt, ext['cad']))

    print(f"[GPX] Synchro: speed={len(speed_samples)}, track={len(track_samples)}, alt={len(alt_samples)}, "
          f"power={len(power_samples)}, atemp={len(atemp_samples)}, hr={len(hr_samples)}, cad={len(cad_samples)}",
          flush=True)
    return speed_samples, track_samples, alt_samples, power_samples, atemp_samples, hr_samples, cad_samples


def find_gpx_for_video(video_path):
    """
    Szuka pliku .gpx o tej samej nazwie co wideo, w tym samym katalogu.

    Args:
        video_path: ścieżka do pliku wideo

    Returns:
        Path do pliku .gpx lub None
    """
    video_path = Path(video_path)
    candidates = [
        video_path.with_suffix('.gpx'),
        video_path.with_suffix('.GPX'),
        video_path.parent / f"{video_path.stem}.gpx",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def process_gpx(video_path, video_start_dt=None):
    """
    Ładuje plik GPX dla danego wideo i synchronizuje go z osią czasu.

    Args:
        video_path: ścieżka do pliku wideo
        video_start_dt: datetime UTC początku filmu (opcjonalny)

    Returns:
        (speed_samples, track_samples, alt_samples,
         power_samples, atemp_samples, hr_samples, cad_samples) lub None
    """
    gpx_path = find_gpx_for_video(video_path)
    if gpx_path is None:
        return None

    points = parse_gpx(gpx_path)
    if points is None:
        return None

    return sync_gpx_to_video(points, video_start_dt)
