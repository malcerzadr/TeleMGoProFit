#!/usr/bin/env python3
"""Obsługa plików FIT (Garmin) dla TeleM – parsowanie, synchronizacja z wideo."""

import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import fitparse
except ImportError:
    fitparse = None


# Konwersja semicircles → stopnie
_SEMICIRC_DEG = 180.0 / 2**31


def parse_fit(fit_path):
    """
    Parsuje plik FIT i zwraca listę punktów (datetime UTC, lat, lon, alt, speed, hr, cad, power, atemp)
    lub None w przypadku błędu.

    - lat/lon: w stopniach dziesiętnych (konwersja z semicircles)
    - alt:     altitude w metrach (lub None)
    - speed:   speed w km/h (z m/s)
    - hr:      heart rate w bpm (lub None)
    - cad:     cadence w rpm (lub None)
    - power:   power w W (lub None)
    - atemp:   temperature w °C (lub None)
    """
    if fitparse is None:
        print("[FIT] Brak biblioteki fitparse. Zainstaluj: pip install fitparse", flush=True)
        return None

    try:
        fitfile = fitparse.FitFile(str(fit_path))
    except Exception as exc:
        print(f"[FIT] Błąd otwierania pliku: {exc}", flush=True)
        return None

    points = []
    for record in fitfile.get_messages('record'):
        data = {}
        for field in record:
            data[field.name] = field.value

        timestamp = data.get('timestamp')
        if timestamp is None:
            continue

        # Konwersja timestamp na datetime UTC
        if isinstance(timestamp, datetime):
            dt = timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)
        else:
            # fitparse zwraca int (Unix timestamp)
            dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)

        # Konwersja semicircles → stopnie
        lat_semi = data.get('position_lat')
        lon_semi = data.get('position_long')
        lat = lat_semi * _SEMICIRC_DEG if lat_semi is not None else None
        lon = lon_semi * _SEMICIRC_DEG if lon_semi is not None else None

        # Filtruj śmieci GPS
        if lat is not None and lon is not None:
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                lat = lon = None

        # Altitude (m)
        alt = data.get('altitude')
        if alt is not None:
            try:
                alt = float(alt)
            except (ValueError, TypeError):
                alt = None

        # Speed: FIT przechowuje w m/s → konwersja na km/h
        speed_ms = data.get('speed')
        if speed_ms is not None:
            try:
                speed = float(speed_ms) * 3.6  # m/s → km/h
            except (ValueError, TypeError):
                speed = None
        else:
            speed = None

        # Enhanced speed (niektóre urządzenia Garmin)
        if speed is None:
            speed_ms = data.get('enhanced_speed')
            if speed_ms is not None:
                try:
                    speed = float(speed_ms) * 3.6
                except (ValueError, TypeError):
                    speed = None

        # Heart rate (bpm)
        hr = data.get('heart_rate')
        if hr is not None:
            try:
                hr = int(hr)
            except (ValueError, TypeError):
                hr = None

        # Cadence (rpm)
        cad = data.get('cadence')
        if cad is not None:
            try:
                cad = int(cad)
            except (ValueError, TypeError):
                cad = None

        # Power (W)
        # Power (W) – fallback: curVpower (virtual power używane przez Tacx/Saris itp.)
        power = data.get('power')
        if power is None:
            power = data.get('curVpower')
        if power is None:
            power = data.get('virtual_power')
        if power is not None:
            try:
                power = int(power)
            except (ValueError, TypeError):
                power = None

        # Temperature (°C)
        atemp = data.get('temperature')
        if atemp is not None:
            try:
                atemp = float(atemp)
            except (ValueError, TypeError):
                atemp = None

        points.append((dt, lat, lon, alt, speed, hr, cad, power, atemp))

    if not points:
        print("[FIT] Brak rekordów 'record' w pliku FIT.", flush=True)
        return None

    # Sortuj po czasie
    points.sort(key=lambda x: x[0])

    # Deduplikacja czasu – scalanie przy powtórzeniach
    deduped = []
    for pt in points:
        if not deduped or pt[0] != deduped[-1][0]:
            deduped.append(pt)
        else:
            # scal wartości (bierz pierwszą nie-None)
            merged = list(deduped[-1])
            for i in range(1, len(pt)):
                if pt[i] is not None:
                    merged[i] = pt[i]
            deduped[-1] = tuple(merged)

    print(f"[FIT] Wczytano {len(deduped)} punktów z {Path(fit_path).name}", flush=True)

    # Podsumuj znalezione dane
    found = set()
    for _, lat, lon, alt, speed, hr, cad, power, atemp in deduped:
        if lat is not None and lon is not None:
            found.add('gps')
        if alt is not None:
            found.add('alt')
        if speed is not None:
            found.add('speed')
        if hr is not None:
            found.add('hr')
        if cad is not None:
            found.add('cad')
        if power is not None:
            found.add('power')
        if atemp is not None:
            found.add('atemp')
    if found:
        print(f"[FIT] Znalezione dane: {sorted(found)}", flush=True)

    return deduped


def haversine_m(lat1, lon1, lat2, lon2):
    """Dystans w metrach między dwoma punktami GPS."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def sync_fit_to_video(points, video_start_dt):
    """
    Synchronizuje punkty FIT z osią czasu wideo.

    Args:
        points: lista (datetime, lat, lon, alt, speed, hr, cad, power, atemp) – czasy absolutne UTC
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
    pts_clean = []
    for dt, lat, lon, alt, speed, hr, cad, power, atemp in points:
        dt_naive = dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
        pts_clean.append((dt_naive, lat, lon, alt, speed, hr, cad, power, atemp))

    # --- Próbki prędkości ---
    speed_samples = []
    for pt in pts_clean:
        dt, _, _, _, speed_val, _, _, _, _ = pt
        if speed_val is not None:
            speed_samples.append((dt, speed_val))

    # Jeśli nie ma bezpośrednich próbek speed, licz z GPS
    if not speed_samples:
        for i in range(1, len(pts_clean)):
            dt1, lat1, lon1, _, _, _, _, _, _ = pts_clean[i - 1]
            dt2, lat2, lon2, _, _, _, _, _, _ = pts_clean[i]
            if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
                continue
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
    for i, (dt, lat, lon, _, _, _, _, _, _) in enumerate(pts_clean):
        if lat is None or lon is None:
            if track_samples:
                # Kontynuuj z ostatnim dystansem
                track_samples.append((dt, total_m))
            continue
        if i > 0:
            prev_lat, prev_lon = pts_clean[i - 1][1], pts_clean[i - 1][2]
            if prev_lat is not None and prev_lon is not None:
                total_m += haversine_m(prev_lat, prev_lon, lat, lon)
        track_samples.append((dt, total_m))

    # --- Próbki wysokości ---
    alt_samples = []
    for dt, _, _, alt_val, _, _, _, _, _ in pts_clean:
        if alt_val is not None:
            alt_samples.append((dt, alt_val))

    # --- Rozszerzenia: power, atemp, hr, cad ---
    power_samples = []
    atemp_samples = []
    hr_samples = []
    cad_samples = []
    for dt, _, _, _, _, hr_val, cad_val, power_val, atemp_val in pts_clean:
        if power_val is not None:
            power_samples.append((dt, float(power_val)))
        if atemp_val is not None:
            atemp_samples.append((dt, float(atemp_val)))
        if hr_val is not None:
            hr_samples.append((dt, float(hr_val)))
        if cad_val is not None:
            cad_samples.append((dt, float(cad_val)))

    print(f"[FIT] Synchro: speed={len(speed_samples)}, track={len(track_samples)}, alt={len(alt_samples)}, "
          f"power={len(power_samples)}, atemp={len(atemp_samples)}, hr={len(hr_samples)}, cad={len(cad_samples)}",
          flush=True)

    return speed_samples, track_samples, alt_samples, power_samples, atemp_samples, hr_samples, cad_samples


def find_fit_for_video(video_path):
    """
    Szuka pliku .fit o tej samej nazwie co wideo, w tym samym katalogu.

    Args:
        video_path: ścieżka do pliku wideo

    Returns:
        Path do pliku .fit lub None
    """
    video_path = Path(video_path)
    candidates = [
        video_path.with_suffix('.fit'),
        video_path.with_suffix('.FIT'),
        video_path.parent / f"{video_path.stem}.fit",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def process_fit(video_path, video_start_dt=None):
    """
    Ładuje plik FIT dla danego wideo i synchronizuje go z osią czasu.

    Args:
        video_path: ścieżka do pliku wideo
        video_start_dt: datetime UTC początku filmu (opcjonalny)

    Returns:
        (speed_samples, track_samples, alt_samples,
         power_samples, atemp_samples, hr_samples, cad_samples) lub None
    """
    fit_path = find_fit_for_video(video_path)
    if fit_path is None:
        return None

    points = parse_fit(fit_path)
    if points is None:
        return None

    return sync_fit_to_video(points, video_start_dt)
