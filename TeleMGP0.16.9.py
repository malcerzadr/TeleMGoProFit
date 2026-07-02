#!/usr/bin/env python3
import struct
import ast
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import shlex
from bisect import bisect_left
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk
except ImportError:
    print("Błąd: brakuje Pillow. Zainstaluj: python -m pip install pillow", file=sys.stderr)
    sys.exit(1)

try:
    import orjson
except ImportError:
    orjson = None

try:
    from telemetry_gpx import process_gpx, find_gpx_for_video, parse_gpx, sync_gpx_to_video
    _GPX_AVAILABLE = True
except ImportError:
    _GPX_AVAILABLE = False
    def process_gpx(video_path, video_start_dt=None):  # noqa: E302
        return None
    def find_gpx_for_video(video_path):  # noqa: E302
        return None
    def parse_gpx(path):  # noqa: E302
        return None
    def sync_gpx_to_video(points, video_start_dt):  # noqa: E302
        return None, None, None, None, None, None, None

try:
    from telemetry_fit import process_fit, find_fit_for_video, parse_fit, sync_fit_to_video
    _FIT_AVAILABLE = True
except ImportError:
    _FIT_AVAILABLE = False
    def process_fit(video_path, video_start_dt=None):  # noqa: E302
        return None
    def find_fit_for_video(video_path):  # noqa: E302
        return None
    def parse_fit(path):  # noqa: E302
        return None
    def sync_fit_to_video(points, video_start_dt):  # noqa: E302
        return None, None, None, None, None, None, None

APP_VERSION = "0.16.9"
RESOLUTION_OPTIONS = ['source', '8k', '5.3k', '4k', '1080p', '720p', '480p']
ENCODER_OPTIONS = ['nv', 'intel', 'cpu']
GPS_OPTIONS = ['3d', '2d']
ROTATION_OPTIONS = ['auto', '0', '90', '180', '270']
SMOOTHING_WINDOW = 5
# Lista dostępnych źródeł telemetrii dla wskaźników (łatwo rozszerzyć o 'fit')
TELEMETRY_SOURCES = ['gpmf', 'gpx', 'fit']


WORKER_CACHE = {}
FONT_CACHE = {}
RESOLUTION_MAP = {
    'source': None,
    '8k': (7680, 4320),
    '5.3k': (5312, 2988),
    '4k': (3840, 2160),
    '1080p': (1920, 1080),
    '720p': (1280, 720),
    '480p': (854, 480),
}

def generate_history_chart(history_values, width, height, line_color=(255, 0, 0), line_thickness=3, fill_alpha=50, fill_color=None, current_index=None, cursor_color=(255, 255, 255), show_axes=True, time_labels=None, value_labels=None):
    """
    Generuje uniwersalny wykres liniowy z przezroczystym wypełnieniem, grubszą linią, osiami i etykietami.
    Opcjonalnie rysuje pionową linię wskazującą aktualną pozycję.
    
    :param history_values: Lista wartości (float/int) do narysowania (np. z ostatnich X sekund lub całej trasy)
    :param width: Szerokość wynikowego obrazka w pikselach
    :param height: Wysokość wynikowego obrazka w pikselach
    :param line_color: Krotka RGB dla głównej linii (np. (255, 0, 0) dla tętna)
    :param line_thickness: Grubość górnej linii wykresu
    :param fill_alpha: Przezroczystość wypełnienia od 0 (całkowicie przezroczyste) do 255 (pełne)
    :param current_index: Indeks aktualnej pozycji w danych (None = brak kursora)
    :param cursor_color: Kolor pionowej linii kursora (domyślnie biały)
    :param show_axes: Czy rysować osie z etykietami
    :param time_labels: Lista 5 stringów – etykiety osi X (czasu)
    :param value_labels: Lista stringów dla osi Y (domyślnie [min, max] jeśli nie podano)
    :return: Obiekt PIL.Image (RGBA) z wygenerowanym wykresem
    """
    # Marginesy na osie i etykiety
    axis_left_margin = 50 if show_axes else 0
    axis_bottom_margin = 22 if show_axes else 0
    axis_top_margin = 4
    axis_right_margin = 4

    # Jeśli nie ma danych lub jest ich za mało, narysuj same osie na pustym tle
    has_data = history_values and len(history_values) >= 2

    # Obliczenie skrajnych wartości Y (nawet jeśli nie ma danych – dla osi)
    if has_data:
        min_val = min(history_values)
        max_val = max(history_values)
    else:
        min_val = 0.0
        max_val = 100.0
    val_range = max_val - min_val
    if val_range == 0:
        val_range = 1.0

    num_points = len(history_values) if has_data else 0

    # Obszar wykresu (plot area – wewnątrz osi)
    plot_x1 = axis_left_margin
    plot_y1 = axis_top_margin
    plot_x2 = width - axis_right_margin
    plot_y2 = height - axis_bottom_margin
    plot_w = plot_x2 - plot_x1
    plot_h = plot_y2 - plot_y1
    if plot_w <= 0:
        plot_w = 1
    if plot_h <= 0:
        plot_h = 1

    # Tworzymy przezroczyste płótno
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # --- RYSOWANIE OSI ---
    if show_axes:
        axis_color = (180, 180, 180, 220)
        tick_color = (150, 150, 150, 200)
        label_color = (200, 200, 200, 240)

        # Oś Y (pionowa, lewa strona)
        draw.line((plot_x1, plot_y1, plot_x1, plot_y2), fill=axis_color, width=1)

        # Oś X (pozioma, dolna)
        draw.line((plot_x1, plot_y2, plot_x2, plot_y2), fill=axis_color, width=1)

        # Etykiety osi Y – wartości min i max
        try:
            font_axis = load_font_cache_small(10)
        except Exception:
            font_axis = None
        y_label_values = value_labels if value_labels else [f"{min_val:.0f}", f"{max_val:.0f}"]
        y_positions = [plot_y2, plot_y1]  # dół = min, góra = max

        for i, (lbl, yp) in enumerate(zip(y_label_values, y_positions)):
            if font_axis:
                bbox = draw.textbbox((0, 0), lbl, font=font_axis)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            else:
                tw = len(lbl) * 6
                th = 10
            tx = plot_x1 - tw - 5
            ty = yp - th // 2
            if font_axis:
                draw.text((tx, ty), lbl, fill=label_color, font=font_axis)
            else:
                draw.text((tx, ty), lbl, fill=label_color)

        # Małe ticki poziome na osi Y przy min/max
        draw.line((plot_x1 - 4, plot_y2, plot_x1, plot_y2), fill=tick_color, width=1)
        draw.line((plot_x1 - 4, plot_y1, plot_x1, plot_y1), fill=tick_color, width=1)

        # Etykiety osi X – 5 punktów czasu
        x_labels = time_labels if time_labels else ["0%", "25%", "50%", "75%", "100%"]
        for i, lbl in enumerate(x_labels):
            x = plot_x1 + (plot_w * i / max(1, len(x_labels) - 1))
            # Pionowy tick
            draw.line((x, plot_y2, x, plot_y2 + 4), fill=tick_color, width=1)
            if font_axis:
                bbox = draw.textbbox((0, 0), lbl, font=font_axis)
                tw = bbox[2] - bbox[0]
            else:
                tw = len(lbl) * 6
            tx = x - tw // 2
            ty = plot_y2 + 5
            if font_axis:
                draw.text((tx, ty), lbl, fill=label_color, font=font_axis)
            else:
                draw.text((tx, ty), lbl, fill=label_color)

    if not has_data:
        return img

    # --- 1. OBLICZANIE WSPÓŁRZĘDNYCH PUNKTÓW WYKRESU ---
    points = []
    for i, val in enumerate(history_values):
        x = plot_x1 + (i / (num_points - 1)) * plot_w
        v_margin = line_thickness + 1
        usable_h = plot_h - (2 * v_margin)
        y = plot_y2 - v_margin - ((val - min_val) / val_range) * usable_h
        points.append((x, y))

    # --- 2. PÓŁPRZEZROCZYSTE WYPEŁNIENIE ---
    fill_polygon = list(points)
    fill_polygon.append((plot_x2, plot_y2))
    fill_polygon.append((plot_x1, plot_y2))
    # Jeśli podano osobny fill_color, użyj go; w przeciwnym razie użyj line_color
    actual_fill_rgb = fill_color if fill_color is not None else line_color
    actual_fill = (actual_fill_rgb[0], actual_fill_rgb[1], actual_fill_rgb[2], fill_alpha)

    fill_img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    fill_draw = ImageDraw.Draw(fill_img)
    fill_draw.polygon(fill_polygon, fill=actual_fill)
    img = Image.alpha_composite(img, fill_img)

    # --- 3. LINIA WYKRESU ---
    draw = ImageDraw.Draw(img)
    draw.line(points, fill=(line_color[0], line_color[1], line_color[2], 255), width=line_thickness, joint="round")

    # --- 4. KURSOR (pionowa linia + kółko) ---
    if current_index is not None and 0 <= current_index < num_points:
        cursor_x = points[current_index][0]
        draw.line(
            (cursor_x, plot_y1, cursor_x, plot_y2),
            fill=(cursor_color[0], cursor_color[1], cursor_color[2], 200),
            width=max(2, line_thickness)
        )
        py = points[current_index][1]
        dot_r = max(3, line_thickness + 1)
        draw.ellipse(
            (cursor_x - dot_r, py - dot_r, cursor_x + dot_r, py + dot_r),
            fill=(cursor_color[0], cursor_color[1], cursor_color[2], 255),
            outline=(line_color[0], line_color[1], line_color[2], 255)
        )

    return img


def load_font_cache_small(size):
    """Zwraca domyślną czcionkę PIL o danym rozmiarze (z cache). Używane do etykiet osi wykresu."""
    key = ('__builtin_default__', int(size))
    if key in FONT_CACHE:
        return FONT_CACHE[key]
    try:
        font = ImageFont.load_default()
        FONT_CACHE[key] = font
        return font
    except Exception:
        return None

def parse_hex_color(hex_str):
    """Konwertuje string koloru hex (np. '#FF3232' lub 'FF3232') na krotkę RGB. Zwraca None przy błędzie."""
    if not hex_str or not isinstance(hex_str, str):
        return None
    s = hex_str.strip().lstrip('#')
    try:
        if len(s) == 6:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        elif len(s) == 3:
            return (int(s[0], 16) * 17, int(s[1], 16) * 17, int(s[2], 16) * 17)
    except Exception:
        pass
    return None


def get_value_schema():
    return get_common_schema() + [
        ("form", "choice", ["text", "gauge", "bar", "chart"], None, None),
        ("font_size", "float", 0.005, 0.1, 0.001),
        ("size", "float", 0.01, 0.5, 0.001),
        ("thickness", "float", 0.001, 0.05, 0.001),
        ("min_val", "float", 0.0, 1000.0, 1.0),
        ("max_val", "float", 1.0, 10000.0, 1.0),
        ("ticks", "int", 0, 20, 1),
        ("show_value", "bool", None, None, None),
        ("value_offset_x", "float", -0.3, 0.3, 0.001),
        ("value_offset_y", "float", -0.3, 0.3, 0.001),
        ("chart_color", "color", None, None, None),
        ("fill_color", "color", None, None, None),
        ("fill_alpha", "int", 0, 255, 5),
    ]


def extract_iso_samples(records):
    records = ensure_records_list(records)
    samples = []

    for rec in records:
        flat = flatten_record(rec)

        prefixes = set()
        for key in flat.keys():
            if key.startswith("Doc") and (
                key.endswith(":ISOSpeeds")
                or key.endswith(":ISO")
                or key.endswith(":ISOSpeed")
                or key.endswith(":ISOSpeedRatings")
            ):
                prefixes.add(key.split(":")[0])

        for prefix in prefixes:
            iso_raw = (
                flat.get(f"{prefix}:ISOSpeeds")
                or flat.get(f"{prefix}:ISO")
                or flat.get(f"{prefix}:ISOSpeed")
                or flat.get(f"{prefix}:ISOSpeedRatings")
            )

            if iso_raw is None:
                continue

            # Parse all space-separated ISO values (30 values per Doc at 30fps)
            iso_txt = str(iso_raw).strip()
            iso_parts = iso_txt.split()
            if not iso_parts:
                continue
            iso_values = []
            for part in iso_parts:
                val = parse_float_maybe(part)
                if val is not None:
                    iso_values.append(int(round(val)))
            if not iso_values:
                continue

            dt = None
            gps_dt = flat.get(f"{prefix}:GPSDateTime")
            if gps_dt is not None:
                dt = parse_exif_datetime(gps_dt)

            if dt is None:
                st = parse_float_maybe(flat.get(f"{prefix}:SampleTime"))
                if st is not None:
                    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=st)

            if dt is None:
                ts = parse_float_maybe(flat.get(f"{prefix}:TimeStamp"))
                if ts is not None:
                    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=ts)

            if dt is None:
                continue

            # Emit one sample per ISO value with per-frame timing (30fps)
            n_vals = len(iso_values)
            for i, val in enumerate(iso_values):
                if n_vals > 1:
                    frame_dt = dt + timedelta(seconds=i / float(n_vals))
                else:
                    frame_dt = dt
                samples.append((frame_dt, val))

    samples.sort(key=lambda x: x[0])

    deduped = []
    for dt, val in samples:
        if not deduped or dt != deduped[-1][0]:
            deduped.append((dt, val))
        else:
            deduped[-1] = (dt, val)

    return deduped


def extract_exposure_samples(records):
    records = ensure_records_list(records)
    samples = []

    for rec in records:
        flat = flatten_record(rec)

        prefixes = set()
        for key in flat.keys():
            if key.startswith("Doc") and key.endswith(":ExposureTimes"):
                prefixes.add(key.split(":")[0])

        for prefix in prefixes:
            raw = flat.get(f"{prefix}:ExposureTimes")
            if raw is None:
                continue

            # Parse all space-separated exposure values like "1/458 1/458 1/462..."
            txt = str(raw).strip()
            parts = txt.split()
            if not parts:
                continue
            exp_values = []
            for part in parts:
                # Parse "1/xxx" to get denominator
                try:
                    if "/" in part:
                        numerator, denominator = part.split("/")
                        exp_values.append(int(round(float(denominator))))
                    else:
                        val = parse_float_maybe(part)
                        if val is not None:
                            exp_values.append(int(round(val)))
                except Exception:
                    pass
            if not exp_values:
                continue

            dt = None
            gps_dt = flat.get(f"{prefix}:GPSDateTime")
            if gps_dt is not None:
                dt = parse_exif_datetime(gps_dt)

            if dt is None:
                st = parse_float_maybe(flat.get(f"{prefix}:SampleTime"))
                if st is not None:
                    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=st)

            if dt is None:
                ts = parse_float_maybe(flat.get(f"{prefix}:TimeStamp"))
                if ts is not None:
                    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=ts)

            if dt is None:
                continue

            # Emit one sample per exposure value with per-frame timing
            n_vals = len(exp_values)
            for i, val in enumerate(exp_values):
                if n_vals > 1:
                    frame_dt = dt + timedelta(seconds=i / float(n_vals))
                else:
                    frame_dt = dt
                samples.append((frame_dt, val))

    samples.sort(key=lambda x: x[0])

    deduped = []
    for dt, val in samples:
        if not deduped or dt != deduped[-1][0]:
            deduped.append((dt, val))
        else:
            deduped[-1] = (dt, val)

    return deduped


def extract_temperature_samples(records):
    records = ensure_records_list(records)
    samples = []

    for rec in records:
        flat = flatten_record(rec)

        prefixes = set()
        for key in flat.keys():
            if key.startswith("Doc") and key.endswith(":CameraTemperature"):
                prefixes.add(key.split(":")[0])

        for prefix in prefixes:
            raw = flat.get(f"{prefix}:CameraTemperature")
            if raw is None:
                continue

            # Parse value like "55.353515625 C" → 55
            temp_val = parse_float_maybe(raw)
            if temp_val is None:
                continue
            temp_val = int(round(temp_val))

            dt = None
            gps_dt = flat.get(f"{prefix}:GPSDateTime")
            if gps_dt is not None:
                dt = parse_exif_datetime(gps_dt)

            if dt is None:
                st = parse_float_maybe(flat.get(f"{prefix}:SampleTime"))
                if st is not None:
                    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=st)

            if dt is None:
                ts = parse_float_maybe(flat.get(f"{prefix}:TimeStamp"))
                if ts is not None:
                    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=ts)

            if dt is None:
                continue

            samples.append((dt, temp_val))

    samples.sort(key=lambda x: x[0])

    deduped = []
    for dt, val in samples:
        if not deduped or dt != deduped[-1][0]:
            deduped.append((dt, val))
        else:
            deduped[-1] = (dt, val)

    return deduped


def load_telemetry_exiftool(video_path):
    cmd = [
        "exiftool",
        "-ee",
        "-G3",
        "-j",
        video_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError("❌ ExifTool error:\n" + result.stderr)

    data = json.loads(result.stdout)

    if not data:
        return {}

    # ExifTool zwraca listę → bierzemy pierwszy element
    return data[0]


def get_common_schema():
    return [
        ("enabled", "bool", None, None, None),
        ("label", "text", None, None, None),
        ("x", "float", 0.0, 1.0, 0.001),
        ("y", "float", 0.0, 1.0, 0.001),
        ("rotation", "choice", [0, 90], None, None),
    ]


BUILTIN_FIELDS = {
    "time_block": get_common_schema() + [
        ("font_label", "float", 0.006, 0.03, 0.001),
        ("font_date", "float", 0.008, 0.05, 0.001),
        ("font_time", "float", 0.008, 0.05, 0.001),
    ],
    "speed_visual": get_value_schema() + [
        ("source", "choice", TELEMETRY_SOURCES, None, None),
    ],
    "speed_text":   get_value_schema() + [
        ("source", "choice", TELEMETRY_SOURCES, None, None),
    ],
    "dist_visual": get_value_schema() + [
        ("source", "choice", TELEMETRY_SOURCES, None, None),
        ("show_range_labels", "bool", None, None, None),
        ("range_label_offset_x", "float", -0.2, 0.2, 0.001),
        ("range_label_offset_y", "float", -0.2, 0.2, 0.001),
        ("range_label_spread_x", "float", -0.2, 0.2, 0.001),
    ],
    "dist_text":    get_value_schema() + [
        ("source", "choice", TELEMETRY_SOURCES, None, None),
    ],
    "alt_visual": get_value_schema() + [
        ("source", "choice", TELEMETRY_SOURCES, None, None),
        ("show_range_labels", "bool", None, None, None),
        ("range_label_offset_x", "float", -0.2, 0.2, 0.001),
        ("range_label_offset_y", "float", -0.2, 0.2, 0.001),
        ("range_label_spread_x", "float", -0.2, 0.2, 0.001),
    ],
    "alt_text":    get_value_schema() + [
        ("source", "choice", TELEMETRY_SOURCES, None, None),
    ],
    "iso_text":    get_value_schema(),
    "exposure_text": get_value_schema(),
    "temp_text":     get_value_schema(),
    "power_text":    get_value_schema(),
    "atemp_text":    get_value_schema(),
    "hr_text":       get_value_schema(),
    "cad_text":      get_value_schema(),
}

FIELD_LABELS = {
    "enabled": "włączony",
    "label": "etykieta",
    "x": "pozycja X",
    "y": "pozycja Y",
    "rotation": "obrót",
    "form": "forma (text/gauge/bar/chart)",
    "font_size": "rozmiar czcionki",
    "font_label": "czcionka etykieta",
    "font_date": "czcionka data",
    "font_time": "czcionka czas",
    "size": "rozmiar",
    "thickness": "grubość",
    "min_val": "min",
    "max_val": "max",
    "ticks": "podziałki",
    "show_value": "pokaż wartość",
    "source": "źródło",
    "show_range_labels": "pokaż zakres",
    "range_label_offset_x": "Pozycja X",
    "range_label_offset_y": "Pozycja Y",
    "range_label_spread_x": "Rozstaw X",
    "chart_color": "Kolor wykresu",
    "fill_color": "Kolor wypełnienia",
    "fill_alpha": "Wypełnienie α",
    "value_offset_x": "Offset X wartości",
    "value_offset_y": "Offset Y wartości",
    "text": "treść tekstu",
    "color": "kolor tekstu",
}

GPX_EXT_FIELDS = ['power_text', 'atemp_text', 'hr_text', 'cad_text']
GPX_EXT_LABELS = {
    'power_text': 'Moc (W)',
    'atemp_text': 'Temp. otocz.',
    'hr_text': 'Puls (BPM)',
    'cad_text': 'Kadencja',
}

TELEMETRY_TAGS = [
    '-GPSDateTime', '-GPSSpeed', '-GPSSpeed3D',
    '-SampleTime', '-TimeStamp',
    '-GPSLatitude', '-GPSLongitude', '-GPSAltitude',
    '-ISOSpeed', '-ISOSpeedRatings',
    '-CameraTemperature', '-ExposureTimes',
]

def speed_at_time(samples, target_dt):
    if not samples:
        return 0.0

    times = [dt for dt, _ in samples]

    idx = bisect_left(times, target_dt)

    if idx == 0:
        return max(0.0, samples[0][1])
    if idx >= len(samples):
        return max(0.0, samples[-1][1])

    t0, s0 = samples[idx - 1]
    t1, s1 = samples[idx]

    dt_total = (t1 - t0).total_seconds()
    if dt_total == 0:
        return max(0.0, s0)

    ratio = (target_dt - t0).total_seconds() / dt_total
    return max(0.0, s0 + (s1 - s0) * ratio)

def smooth_speed_values(values, window=5):
    out = []

    for i in range(len(values)):
        acc = 0.0
        count = 0

        for j in range(max(0, i - window), min(len(values), i + window + 1)):
            acc += values[j]
            count += 1

        out.append(acc / count if count else values[i])

    return out


def json_loads(text):
    if orjson is not None:
        return orjson.loads(text)
    return json.loads(text)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip())
    return p.stdout


def run_live(cmd):
    p = subprocess.run(cmd)
    if p.returncode != 0:
        raise RuntimeError(f'Polecenie zakończone błędem: {p.returncode}')


def find_local_tool(base_dir, names):
    for name in names:
        p = base_dir / name
        if p.exists():
            return p
    return None


def find_executable(name, extra_candidates=None):
    p = shutil.which(name)
    if p:
        return p
    extra_candidates = extra_candidates or []
    for candidate in extra_candidates:
        if Path(candidate).exists():
            return str(Path(candidate))
    return None


def sanitize_output_path(path_text):
    txt = str(path_text).strip()
    while txt.endswith('.'):
        txt = txt[:-1]
    return Path(txt)


def _set_hidden(path):
    try:
        if os.name == 'nt':
            import ctypes
            FILE_ATTRIBUTE_HIDDEN = 0x02
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            if attrs != -1:
                ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


def find_metadata_json(video_path):
    return Path(video_path).with_suffix(".json")


def find_metadata_json_for_write(video_path):
    video_path = Path(video_path)
    # Prefer an obvious JSON alongside the video
    same_dir = video_path.with_suffix('.json')
    # Hidden: same dir with a dot prefix to hide on Unix, or a hidden subfolder on Windows
    hidden_dir = Path(tempfile.gettempdir()) / 'TeleM' / 'telemetry_hidden'
    hidden = hidden_dir / f"{video_path.stem}.json"
    fallback_dir = Path(tempfile.gettempdir()) / 'TeleM' / 'telemetry_hidden'
    fallback = fallback_dir / f"{video_path.stem}.json"
    for candidate in (same_dir, hidden, fallback):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with open(candidate, 'a', encoding='utf-8'):
                pass
            print(f'find_metadata_json_for_write: selected {candidate}', flush=True)
            return candidate
        except Exception as exc:
            print(f'find_metadata_json_for_write: cannot use {candidate}: {exc}', flush=True)
            continue

    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'_{video_path.stem}.json', dir=tempfile.gettempdir())
        tmp.close()
        print(f'find_metadata_json_for_write: using temporary file {tmp.name}', flush=True)
        return Path(tmp.name)
    except Exception as exc:
        print(f'find_metadata_json_for_write: fallback temporary file failed: {exc}', flush=True)
        return hidden


def write_records_to_json(out_json, records):
    try:
        out_json_parent = Path(out_json).parent
        out_json_parent.mkdir(parents=True, exist_ok=True)
        with open(out_json, 'w', encoding='utf-8') as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        # Do not hide JSON metadata files by default; keep them visible in the source folder.
        print(f'write_records_to_json: wrote records to {out_json}', flush=True)
        return Path(out_json)
    except Exception as exc:
        print(f'write_records_to_json: failed writing to {out_json}: {exc}', flush=True)

    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'_{Path(out_json).stem}.json', dir=tempfile.gettempdir())
        tmp.close()
        with open(tmp.name, 'w', encoding='utf-8') as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        _set_hidden(tmp.name)
        print(f'write_records_to_json: wrote records to fallback temp {tmp.name}', flush=True)
        return Path(tmp.name)
    except Exception as exc:
        print(f'write_records_to_json: fallback write failed: {exc}', flush=True)
        raise RuntimeError(f'Nie udało się zapisać metadanych JSON: {exc}') from exc


def find_gpmf_stream_index(video_path, ffprobe_exe='ffprobe'):
    try:
        out = run([ffprobe_exe, '-v', 'error', '-show_streams', '-of', 'json', str(video_path)])
        data = json.loads(out)
        fallback_index = None
        for stream in data.get('streams', []):
            codec_name = str(stream.get('codec_name', '')).lower()
            codec_tag  = str(stream.get('codec_tag_string', '')).lower()
            handler    = str(stream.get('tags', {}).get('handler_name', '')).lower()
            if any(x in codec_name for x in ('gpmd', 'gpmf')) or any(x in codec_tag for x in ('gpmd', 'gpmf')):
                return int(stream.get('index', 0))
            if stream.get('codec_type', '').lower() == 'data':
                if 'go pro' in handler and 'met' in handler:
                    return int(stream.get('index', 0))
                if fallback_index is None:
                    fallback_index = int(stream.get('index', 0))
        return fallback_index
    except Exception:
        pass
    return None


def extract_gpmf(video_path, ffmpeg_exe='ffmpeg', ffprobe_exe='ffprobe'):
    stream_index = find_gpmf_stream_index(video_path, ffprobe_exe=ffprobe_exe)
    if stream_index is None:
        raise RuntimeError('Nie znaleziono strumienia GPMF w pliku.')

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.bin')
    tmp.close()
    try:
        cmd = [
            ffmpeg_exe, '-y', '-i', str(video_path),
            '-map', f'0:{stream_index}', '-c', 'copy', '-f', 'data', tmp.name
        ]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if p.returncode != 0:
            raise RuntimeError(
                'Błąd ekstrakcji strumienia GPMF przez ffmpeg.\n'
                f'Command: {shlex.join(cmd)}\n'
                f'Return code: {p.returncode}\n'
                f'stderr:\n{(p.stderr or "").strip()}'
            )
        with open(tmp.name, 'rb') as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def decode_gpmf(t, repeat, payload):
    try:
        if t == 'c':
            return payload.decode('ascii', errors='ignore').rstrip('\x00')
        if t == 'b':
            fmt = '>' + 'b' * repeat
            vals = struct.unpack(fmt, payload)
        elif t == 'B':
            fmt = '>' + 'B' * repeat
            vals = struct.unpack(fmt, payload)
        elif t == 's':
            fmt = '>' + 'h' * repeat
            vals = struct.unpack(fmt, payload)
        elif t == 'S':
            fmt = '>' + 'H' * repeat
            vals = struct.unpack(fmt, payload)
        elif t == 'l':
            fmt = '>' + 'i' * repeat
            vals = struct.unpack(fmt, payload)
        elif t == 'L':
            fmt = '>' + 'I' * repeat
            vals = struct.unpack(fmt, payload)
        elif t == 'f':
            fmt = '>' + 'f' * repeat
            vals = struct.unpack(fmt, payload)
        elif t == 'd':
            fmt = '>' + 'd' * repeat
            vals = struct.unpack(fmt, payload)
        elif t == 'Q':
            fmt = '>' + 'Q' * repeat
            vals = struct.unpack(fmt, payload)
        elif t == 'J':
            fmt = '>' + 'q' * repeat
            vals = struct.unpack(fmt, payload)
        else:
            # debug nieznanych typów
            # print(f"[GPMF] Nieznany typ GPMF: {t} dla klucza, repeat={repeat}")
            return payload

        if repeat == 1:
            return vals[0]
        return vals
    except Exception:
        return payload


def parse_gpmf(data, offset=0):
    results = []
    while offset + 8 <= len(data):
        try:
            key = data[offset:offset+4].decode('ascii', errors='ignore')
        except Exception:
            break
        t = chr(data[offset+4])
        size = data[offset+5]
        if offset + 8 > len(data):
            break
        try:
            repeat = struct.unpack('>H', data[offset+6:offset+8])[0]
        except struct.error:
            break
        payload_size = size * repeat
        # zabezpieczenie przed śmieciowymi danymi
        if payload_size < 0 or payload_size > len(data):
            break
        payload_start = offset + 8
        payload_end = payload_start + payload_size
        if payload_end > len(data):
            break
        payload = data[payload_start:payload_end]
        padded = (payload_size + 3) & ~3
        offset = payload_start + padded
        if t == '\x00':
            results.extend(parse_gpmf(payload, 0))
        else:
            results.append((key, decode_gpmf(t, repeat, payload)))
    return results


def gpmf_to_exiftool_json(video_path, ffmpeg_exe='ffmpeg', ffprobe_exe='ffprobe'):
    data = extract_gpmf(video_path, ffmpeg_exe=ffmpeg_exe, ffprobe_exe=ffprobe_exe)
    parsed = parse_gpmf(data)
    # DEBUG: zwarty wydruk kluczy GPMF
    keys_found = sorted(set(k for k, v in parsed))
    has_gps9 = 'GPS9' in keys_found
    has_gps5 = 'GPS5' in keys_found
    has_gpsu = 'GPSU' in keys_found
    print(f'[GPMF] Klucze: {keys_found}', flush=True)
    print(f'[GPMF] GPS5={has_gps5} GPS9={has_gps9} GPSU={has_gpsu}  rekordów={len(parsed)}', flush=True)
    return to_exiftool_json(parsed, str(video_path))


def try_decode_8byte_stmp(raw):
    """Próbuje zdekodować 8-bajtowy STMP jako uint64 (mikrosekundy) lub double."""
    if not isinstance(raw, bytes) or len(raw) != 8:
        return None
    try:
        # próbuj jako uint64 (microsekundy → sekundy)
        us = struct.unpack('>Q', raw)[0]
        if us > 0:
            return us / 1_000_000.0
    except Exception:
        pass
    try:
        # próbuj jako double
        return struct.unpack('>d', raw)[0]
    except Exception:
        pass
    return None


def _decode_gps9_values(val, scal):
    """Dekoduje GPS9: 9 wartości int32 na próbkę, skalowanych przez krotkę SCAL.
    Zwraca listę (lat, lon, alt_m, s2d, s3d)."""
    results = []
    raw_vals = None
    if isinstance(val, (list, tuple)):
        raw_vals = list(val)
    elif isinstance(val, bytes) and len(val) > 0:
        # Spróbuj zinterpretować jako int32 (4 bajty na wartość)
        try:
            n = len(val) // 4
            raw_vals = list(struct.unpack(f'>{n}i', val[:n*4]))
        except Exception:
            return results

    if not raw_vals or len(raw_vals) < 9:
        return results

    if not isinstance(scal, (list, tuple)):
        scal = (scal,) * 9
    # Uzupełnij SCAL do 9 elementów
    s = list(scal) + [1] * max(0, 9 - len(scal))

    for i in range(0, len(raw_vals) - len(raw_vals) % 9, 9):
        chunk = raw_vals[i:i+9]
        try:
            lat = chunk[0] / s[0] if s[0] else 0.0
            lon = chunk[1] / s[1] if s[1] else 0.0
            alt = chunk[2] / s[2] if s[2] else 0.0
            s2d = chunk[4] / s[4] if s[4] else 0.0
            s3d = chunk[5] / s[5] if s[5] else 0.0
            results.append((lat, lon, alt, s2d, s3d))
        except Exception:
            continue
    return results


def to_exiftool_json(parsed, source_file):
    out = {'SourceFile': source_file}
    doc = 1
    scal = None
    last_gpsu_dt = None
    current_block_start_dt = None

    def _fmt_dt(dt):
        return dt.astimezone(timezone.utc).strftime("%Y:%m:%d %H:%M:%S.%f")[:-3]

    def _parse_int32_values(val):
        if isinstance(val, (list, tuple)):
            return list(val)
        if isinstance(val, bytes):
            try:
                n = len(val) // 4
                return list(struct.unpack(f'>{n}i', val[:n * 4]))
            except Exception:
                return []
        return []

    for key, val in parsed:
        if key == 'SCAL':
            scal = val

        elif key == 'GPSU':
            try:
                if isinstance(val, (list, tuple)) and len(val) >= 7:
                    year = int(val[0])
                    month = int(val[1])
                    day = int(val[2])
                    hour = int(val[3])
                    minute = int(val[4])
                    sec = int(val[5])
                    ms = int(val[6])

                    last_gpsu_dt = datetime(
                        year, month, day, hour, minute, sec, ms * 1000,
                        tzinfo=timezone.utc
                    )
                    current_block_start_dt = last_gpsu_dt
                    out[f'Doc{doc}:GPSDateTime'] = _fmt_dt(last_gpsu_dt)
                    out[f'Doc{doc}:SampleTime'] = "0.000"
                    print("✅ GPSU decoded:", out[f'Doc{doc}:GPSDateTime'], flush=True)
            except Exception as e:
                print("❌ GPSU decode failed:", e, flush=True)

        elif key in ('GPS5', 'GPS9'):
            values = _parse_int32_values(val)
            step = 5 if key == 'GPS5' else 9

            if len(values) < step:
                continue

            usable = len(values) - (len(values) % step)

            if isinstance(scal, (list, tuple)):
                s = list(scal)[:step]
                if len(s) < step:
                    s += [1] * (step - len(s))
            else:
                s = [1] * step

            block_doc = doc

            for i in range(0, usable, step):
                chunk = values[i:i + step]

                try:
                    if key == 'GPS5':
                        lat = chunk[0] / s[0] if s[0] else 0.0
                        lon = chunk[1] / s[1] if s[1] else 0.0
                        alt = chunk[2] / s[2] if s[2] else 0.0
                        s2d = chunk[3] / s[3] if s[3] else 0.0
                        s3d = chunk[4] / s[4] if s[4] else 0.0
                        ts_val = None
                    else:
                        lat = chunk[0] / s[0] if s[0] else 0.0
                        lon = chunk[1] / s[1] if s[1] else 0.0
                        alt = chunk[2] / s[2] if s[2] else 0.0
                        s2d = chunk[4] / s[4] if s[4] else 0.0
                        s3d = chunk[5] / s[5] if s[5] else 0.0
                        ts_val = chunk[8]

                    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        continue

                    if abs(s2d) > 300:
                        s2d = 0.0
                    if abs(s3d) > 300:
                        s3d = 0.0

                    prefix = f'Doc{block_doc}' if i == 0 else f'Doc{block_doc}-{i // step}'

                    out[f'{prefix}:GPSLatitude'] = lat
                    out[f'{prefix}:GPSLongitude'] = lon
                    out[f'{prefix}:GPSAltitude'] = f'{alt} m'
                    out[f'{prefix}:GPSSpeed'] = s2d
                    out[f'{prefix}:GPSSpeed3D'] = s3d

                    if ts_val is not None:
                        out[f'{prefix}:TimeStamp'] = str(ts_val)

                    if current_block_start_dt is not None and key == 'GPS9':
                        sample_idx = i // step
                        sample_dt = current_block_start_dt + timedelta(seconds=sample_idx * 0.1)
                        out[f'{prefix}:GPSDateTime'] = _fmt_dt(sample_dt)
                        out[f'{prefix}:SampleTime'] = f'{sample_idx * 0.1:.3f}'

                except Exception:
                    continue

            doc += 1

        elif key == 'GPSA':
            if isinstance(val, bytes):
                out[f'Doc{doc}:GPSAltitudeSystem'] = val.decode('ascii', errors='ignore').strip('\x00')
            else:
                out[f'Doc{doc}:GPSAltitudeSystem'] = str(val)

        elif key == 'TMPC':
            out[f'Doc{doc}:CameraTemperature'] = f'{val} C'

        elif key == 'SHUT':
            if isinstance(val, (list, tuple)):
                exp = ' '.join([f'1/{int(1/x)}' if x != 0 else '0' for x in val])
            else:
                exp = str(val)
            out[f'Doc{doc}:ExposureTimes'] = exp

        elif key == 'ISOE':
            out[f'Doc{doc}:ISO'] = val

        elif key == 'STMP':
            sample_time = val
            if isinstance(val, bytes):
                try:
                    sample_time = float(val.decode('ascii', errors='ignore'))
                except Exception:
                    try:
                        sample_time = float(ast.literal_eval(val.decode('ascii', errors='ignore')))
                    except Exception:
                        sample_time = try_decode_8byte_stmp(val)
            out[f'Doc{doc}:SampleTime'] = sample_time if sample_time is None else str(sample_time)

        elif key == 'TSMP':
            timestamp = val
            if isinstance(val, bytes):
                try:
                    timestamp = float(val.decode('ascii', errors='ignore'))
                except Exception:
                    try:
                        timestamp = float(ast.literal_eval(val.decode('ascii', errors='ignore')))
                    except Exception:
                        timestamp = None
            out[f'Doc{doc}:TimeStamp'] = timestamp

    gps_dt_count = sum(1 for k in out.keys() if k.endswith(':GPSDateTime'))
    print(f'[GPMF] GPSDateTime fields generated: {gps_dt_count}', flush=True)

    return [out]

def format_time(t):
    if isinstance(t, str):
        return t.replace('T', ' ').replace('-', ':').replace('Z', '')
    return str(t)


# Słownik globalny (cache) do zapamiętywania długości poszczególnych plików wideo
_FFPROBE_DURATION_CACHE = {}

def extract_frame(video_paths, timestamp_s, ffmpeg_exe='ffmpeg', ffprobe_exe='ffprobe'):
    if not isinstance(video_paths, list):
        video_paths = [video_paths]

    target_path = video_paths[0]
    target_ts = timestamp_s

    # Znajdź właściwy plik w sekwencji rozdziałów
    current_offset = 0.0
    for p in video_paths:
        # POBIERANIE Z CACHE ZAMIAST CIĄGŁEGO ODPALANIA PROCESU
        if p not in _FFPROBE_DURATION_CACHE:
            info = ffprobe_stream_info(ffprobe_exe, p)
            _FFPROBE_DURATION_CACHE[p] = float(info.get('format', {}).get('duration', 0) or 0)
            
        dur = _FFPROBE_DURATION_CACHE[p]
        
        if current_offset + dur > timestamp_s:
            target_path = p
            target_ts = timestamp_s - current_offset
            break
        current_offset += dur

    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    cmd = [
        ffmpeg_exe, '-ss', str(target_ts),
        '-i', str(target_path),
        '-frames:v', '1', '-q:v', '2',
        '-f', 'image2pipe', '-vcodec', 'png', '-'
    ]
    p = subprocess.run(cmd, capture_output=True, startupinfo=startupinfo)
    if p.returncode != 0 or not p.stdout:
        return None
    return Image.open(io.BytesIO(p.stdout)).convert('RGBA')

def ffprobe_resolution(video_path, ffprobe='ffprobe'):
    out = run([ffprobe, '-v', 'error', '-select_streams', 'v:0',
               '-show_entries', 'stream=width,height', '-of', 'json', str(video_path)])
    data = json.loads(out)
    streams = data.get('streams', [])
    if not streams:
        return 1280, 720
    return int(streams[0].get('width', 1280)), int(streams[0].get('height', 720))


def ffprobe_stream_info(ffprobe_exe, input_file):
    out = run([
        ffprobe_exe, '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=r_frame_rate,avg_frame_rate,width,height:format=duration',
        '-of', 'json', str(input_file)
    ])
    return json.loads(out)


def parse_fps(rate_text):
    if not rate_text or rate_text == '0/0':
        return 30.0
    if '/' in rate_text:
        a, b = rate_text.split('/')
        a, b = float(a), float(b)
        if b == 0:
            return 30.0
        return a / b
    return float(rate_text)


def s(value, base):
    return max(1, int(round(value * base)))


def default_layout(video_width, video_height):
    return {
        "version": 5,
        "global": {"text_outline": 3},
        "custom_texts": [],
        "indicators": {
            "time_block": {
                "enabled": True, "label": "Czas", "x": 0.018, "y": 0.030, "rotation": 0,
                "font_label": 0.0125, "font_date": 0.020, "font_time": 0.020
            },
            "speed_visual": {
                "enabled": True, "label": "", "x": 0.50, "y": 0.78, "rotation": 0, "form": "gauge",
                "font_size": 0.0125, "size": 0.108, "thickness": 0.007, "min_val": 0, "max_val": 60, "ticks": 6,
                "source": "gpmf"
            },
            "speed_text": {
                "enabled": True, "label": "", "x": 0.50, "y": 0.855, "rotation": 0, "form": "text",
                "font_size": 0.042, "size": 0.1, "thickness": 0.001, "min_val": 0, "max_val": 100, "ticks": 0,
                "source": "gpmf"
            },
            "dist_visual": {
                "enabled": True, "label": "", "x": 0.50, "y": 0.925, "rotation": 0, "form": "bar",
                "font_size": 0.0125, "size": 0.20, "thickness": 0.004, "min_val": 0, "max_val": 10, "ticks": 5,
                "show_range_labels": True,
                "range_label_offset_x": -0.112,
                "range_label_offset_y": -0.001,
                "range_label_spread_x": 0.0,
                "value_offset_x": 0.0,
                "value_offset_y": 0.0,
                "source": "gpmf"
            },
            "dist_text": {
                "enabled": True, "label": "", "x": 0.50, "y": 0.955, "rotation": 0, "form": "text",
                "font_size": 0.017, "size": 0.1, "thickness": 0.001, "min_val": 0, "max_val": 100, "ticks": 0,
                "source": "gpmf"
            },
            "alt_visual": {
                "enabled": True, "label": "Alt", "x": 0.04, "y": 0.80, "rotation": 90, "form": "bar",
                "font_size": 0.0125, "size": 0.20, "thickness": 0.006, "min_val": 0, "max_val": 100, "ticks": 5,
                "show_range_labels": True,
                "range_label_offset_x": -0.112,
                "range_label_offset_y": -0.008,
                "range_label_spread_x": 0.0,
                "value_offset_x": 0.0,
                "value_offset_y": 0.0,
                "source": "gpmf"
            },
            "alt_text": {
                "enabled": True, "label": "", "x": 0.025, "y": 0.8, "rotation": 0, "form": "text",
                "font_size": 0.017, "size": 0.1, "thickness": 0.001, "min_val": 0, "max_val": 1000, "ticks": 0,
                "source": "gpmf"
            },
            "iso_text": {
                "enabled": True, "label": "ISO", "x": 0.90, "y": 0.08, "rotation": 0, "form": "text",
                "font_size": 0.018, "size": 0.1, "thickness": 0.001, "min_val": 0, "max_val": 12800, "ticks": 0
            },
            "exposure_text": {
                "enabled": True, "label": "Exp", "x": 0.82, "y": 0.08, "rotation": 0, "form": "text",
                "font_size": 0.018, "size": 0.1, "thickness": 0.001, "min_val": 0, "max_val": 10000, "ticks": 0
            },
            "temp_text": {
                "enabled": True, "label": "Temp", "x": 0.74, "y": 0.08, "rotation": 0, "form": "text",
                "font_size": 0.018, "size": 0.1, "thickness": 0.001, "min_val": 0, "max_val": 100, "ticks": 0
            },
            "power_text": {
                "enabled": True, "label": "Moc", "x": 0.185, "y": 0.08, "rotation": 0, "form": "text",
                "font_size": 0.018, "size": 0.1, "thickness": 0.001, "min_val": 0, "max_val": 1000, "ticks": 0
            },
            "atemp_text": {
                "enabled": True, "label": "ATemp", "x": 0.265, "y": 0.08, "rotation": 0, "form": "text",
                "font_size": 0.018, "size": 0.1, "thickness": 0.001, "min_val": -20, "max_val": 60, "ticks": 0
            },
            "hr_text": {
                "enabled": True, "label": "HR", "x": 0.345, "y": 0.08, "rotation": 0, "form": "text",
                "font_size": 0.018, "size": 0.1, "thickness": 0.001, "min_val": 0, "max_val": 250, "ticks": 0
            },
            "cad_text": {
                "enabled": True, "label": "Cad", "x": 0.41, "y": 0.08, "rotation": 0, "form": "text",
                "font_size": 0.018, "size": 0.1, "thickness": 0.001, "min_val": 0, "max_val": 200, "ticks": 0
            },
        },
        "smoothing": {"method": "moving_average", "strength": 3}
    }


def flatten_value(prefix, value, out):
    if isinstance(value, dict):
        for k, v in value.items():
            nk = f"{prefix}.{k}" if prefix else str(k)
            flatten_value(nk, v, out)
    elif isinstance(value, list):
        if value and all(not isinstance(x, (dict, list)) for x in value):
            out[prefix] = " ".join(str(x) for x in value)
        else:
            for i, v in enumerate(value):
                flatten_value(f"{prefix}[{i}]", v, out)
    else:
        out[prefix] = value


def flatten_record(rec):
    out = {}
    if isinstance(rec, dict):
        for k, v in rec.items():
            flatten_value(str(k), v, out)
    return out


def ensure_records_list(records):
    if isinstance(records, list):
        return records
    if isinstance(records, dict):
        return [records]
    raise RuntimeError('Nieprawidłowy format JSON telemetrii: oczekiwano listy lub słownika.')


def parse_exif_datetime(val):
    if not val:
        return None

    txt = str(val).strip()

    # 🧠 NAJWAŻNIEJSZE: wywalić śmieci
    txt = txt.replace("Z", "").strip()

    try:
        if "." in txt:
            return datetime.strptime(txt, "%Y:%m:%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
        else:
            return datetime.strptime(txt, "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        print("❌ BAD TIME:", repr(val))
        return None

def parse_float_maybe(val):
    if val is None:
        return None
    try:
        return float(val)
    except Exception:
        try:
            return float(str(val).split()[0].replace(",", "."))
        except Exception:
            return None


def parse_gps_coord(val):
    if not val:
        return None

    txt = str(val)

    try:
        parts = txt.replace("deg", "").replace("'", "").replace('"', "").split()
        deg = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
        hemi = parts[3]

        dec = deg + minutes/60 + seconds/3600
        if hemi in ("S", "W"):
            dec = -dec

        return dec
    except Exception:
        print("❌ GPS parse fail:", txt)
        return None


def load_json_with_fallback(path):
    last_error = None
    for enc in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "cp1252"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except Exception as e:
            last_error = e
    raise RuntimeError(f"Nie udało się odczytać JSON z pliku: {path}\nOstatni błąd: {last_error}")


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_gps_anchor(records):
    records = ensure_records_list(records)
    for rec in records:
        if not isinstance(rec, dict):
            continue
        datetimes = get_all_values_by_suffix(rec, "GPSDateTime")
        print("datetimes count:", len(datetimes))
        for prefix, dt_str in datetimes.items():
            dt = parse_exif_datetime(dt_str)
            if dt is None:
                continue
            st_val = get_value_by_suffix_for_prefix(rec, prefix, "SampleTime")
            if st_val is not None:
                st_sec = parse_float_maybe(str(st_val).replace(" s", ""))
                if st_sec is not None:
                    return dt - timedelta(seconds=st_sec)
            ts_val = get_value_by_suffix_for_prefix(rec, prefix, "TimeStamp")
            if ts_val is not None:
                ts_sec = parse_float_maybe(ts_val)
                if ts_sec is not None:
                    return dt - timedelta(seconds=ts_sec)
    return None


def get_all_values_by_suffix(rec, suffix):
    if not rec or not suffix:
        return {}
    flat = flatten_record(rec)
    wanted = ":" + suffix
    return {k[:-len(wanted)]: v for k, v in flat.items()
            if k.startswith("Doc") and k.endswith(wanted)}


def get_value_by_suffix_for_prefix(rec, prefix, suffix):
    if not rec or not prefix or not suffix:
        return None
    return flatten_record(rec).get(f"{prefix}:{suffix}")


def extract_speed_samples(records, prefer_3d=True):
    print("USING PATCHED EXTRACT FUNCTION", flush=True)
    records = ensure_records_list(records)
    samples = []

    for rec in records:
        flat = flatten_record(rec)

        prefixes = set()
        for key in flat.keys():
            if key.startswith("Doc") and (key.endswith(":GPSSpeed") or key.endswith(":GPSSpeed3D")):
                prefixes.add(key.split(":")[0])

        for prefix in prefixes:
            speed_2d = parse_float_maybe(flat.get(f"{prefix}:GPSSpeed"))
            speed_3d = parse_float_maybe(flat.get(f"{prefix}:GPSSpeed3D"))

            if prefer_3d:
                speed = speed_3d if speed_3d not in (None, 0.0) else speed_2d
            else:
                speed = speed_2d if speed_2d not in (None, 0.0) else speed_3d

            if speed is None:
                continue

            dt = None

            gps_dt = flat.get(f"{prefix}:GPSDateTime")
            if gps_dt is not None:
                dt = parse_exif_datetime(gps_dt)

            if dt is None:
                st = parse_float_maybe(flat.get(f"{prefix}:SampleTime"))
                if st is not None:
                    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=st)

            if dt is None:
                ts = parse_float_maybe(flat.get(f"{prefix}:TimeStamp"))
                if ts is not None:
                    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=ts)

            if dt is None:
                continue

            samples.append((dt, max(0.0, speed)))

    samples.sort(key=lambda x: x[0])

    deduped = []
    for dt, val in samples:
        if not deduped or dt != deduped[-1][0]:
            deduped.append((dt, val))
        else:
            deduped[-1] = (dt, val)

    print("DEBUG samples built:", len(deduped), flush=True)
    if deduped:
        print("FIRST SAMPLE:", deduped[0], flush=True)

    gps_dt_prefixes = 0
    for rec in records:
        flat = flatten_record(rec)
        gps_dt_prefixes += sum(1 for k in flat if k.endswith(":GPSDateTime"))
    print("GPSDateTime keys visible to extractor:", gps_dt_prefixes, flush=True)

    return deduped

def extract_altitude_samples(records):
    records = ensure_records_list(records)
    samples = []

    for rec in records:
        flat = flatten_record(rec)

        prefixes = set()
        for key in flat.keys():
            if key.startswith("Doc") and key.endswith(":GPSAltitude"):
                prefixes.add(key.split(":")[0])

        for prefix in prefixes:
            raw_alt = flat.get(f"{prefix}:GPSAltitude")
            alt = parse_float_maybe(raw_alt)

            if alt is None:
                continue

            dt = None

            gps_dt = flat.get(f"{prefix}:GPSDateTime")
            if gps_dt is not None:
                dt = parse_exif_datetime(gps_dt)

            if dt is None:
                st = parse_float_maybe(flat.get(f"{prefix}:SampleTime"))
                if st is not None:
                    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=st)

            if dt is None:
                ts = parse_float_maybe(flat.get(f"{prefix}:TimeStamp"))
                if ts is not None:
                    dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=ts)

            if dt is None:
                continue

            samples.append((dt, alt))

    samples.sort(key=lambda x: x[0])

    deduped = []
    for dt, val in samples:
        if not deduped or dt != deduped[-1][0]:
            deduped.append((dt, val))
        else:
            deduped[-1] = (dt, val)

    return deduped

def extract_track_samples(records):
    records = ensure_records_list(records)
    points = []

    for rec in records:
        flat = flatten_record(rec)

        for key, val in flat.items():
            if not key.endswith(":GPSDateTime"):
                continue

            prefix = key.split(":")[0]

            # ✅ CZAS
            dt = parse_exif_datetime(val)
            if dt is None:
                continue

            # ✅ RAW GPS
            raw_lat = flat.get(f"{prefix}:GPSLatitude")
            raw_lon = flat.get(f"{prefix}:GPSLongitude")

            lat = parse_gps_coord(raw_lat)
            lon = parse_gps_coord(raw_lon)

            if lat is None or lon is None:
                continue

            # ✅ filtr bezpieczeństwa (wycina śmieci)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue

            points.append((dt, lat, lon))

    # ✅ sort
    points.sort(key=lambda x: x[0])

    # ✅ dedupe
    deduped = []
    for dt, lat, lon in points:
        if not deduped or dt != deduped[-1][0]:
            deduped.append((dt, lat, lon))
        else:
            deduped[-1] = (dt, lat, lon)

    # ✅ licz dystans (cumulative)
    cumulative = []
    total_m = 0.0

    for i, (dt, lat, lon) in enumerate(deduped):
        if i > 0:
            _, prev_lat, prev_lon = deduped[i - 1]
            total_m += haversine_m(prev_lat, prev_lon, lat, lon)

        cumulative.append((dt, total_m))

    return cumulative


def moving_average(values, window):
    if window <= 1 or not values:
        return values[:]
    out, acc, queue = [], 0.0, []
    for v in values:
        queue.append(v)
        acc += v
        if len(queue) > window:
            acc -= queue.pop(0)
        out.append(acc / len(queue))
    return out


def exponential_moving_average(values, alpha):
    if not values:
        return values[:]
    alpha = max(0.01, min(1.0, alpha))
    out = [values[0]]
    prev = values[0]
    for v in values[1:]:
        prev = alpha * v + (1.0 - alpha) * prev
        out.append(prev)
    return out


def smooth_speed_samples(samples, method='off', strength=3):
    if not samples or method == 'off':
        return samples
    times = [t for t, _ in samples]
    vals = [v for _, v in samples]
    if method == 'moving_average':
        smoothed = moving_average(vals, max(1, int(round(strength))))
    elif method == 'ema':
        smoothed = exponential_moving_average(vals, max(0.05, min(1.0, float(strength))))
    else:
        smoothed = vals
    return list(zip(times, smoothed))


def interpolate_speed(samples, target_dt):
    # ✅ wyrównanie timezone
    if target_dt.tzinfo is not None:    
        target_dt = target_dt.replace(tzinfo=None)
    samples = [(dt.replace(tzinfo=None), s) for dt, s in samples]

    if not samples:
        return 0.0
    times = [dt for dt, _ in samples]
    idx = bisect_left(times, target_dt)
    if idx <= 0:
        # Jeśli szukany czas jest przed pierwszą próbką GPS, zwracamy 0.0
        if samples and target_dt < samples[0][0]:
            return 0.0
        return max(0.0, samples[0][1]) if samples else 0.0
    if idx >= len(samples):
        return max(0.0, samples[-1][1])
    t1, s1 = samples[idx - 1]
    t2, s2 = samples[idx]
    dt_total = (t2 - t1).total_seconds()
    if dt_total <= 0:
        return max(0.0, s1)
    return max(0.0, s1 + (s2 - s1) * (target_dt - t1).total_seconds() / dt_total)


def interpolate_distance(track_samples, target_dt):
    if not track_samples:
        return 0.0
    # Normalize timezones – GPX samples are stored as naive UTC,
    # but target_dt may arrive as timezone-aware from the render pipeline.
    if target_dt.tzinfo is not None:
        target_dt = target_dt.replace(tzinfo=None)
    track_samples = [(dt.replace(tzinfo=None), d) for dt, d in track_samples]
    times = [dt for dt, _ in track_samples]
    idx = bisect_left(times, target_dt)
    if idx <= 0:
        # Dystans przed pierwszym fixem to 0.0
        if track_samples and target_dt < track_samples[0][0]:
            return 0.0
        return track_samples[0][1] if track_samples else 0.0
    if idx >= len(track_samples):
        return track_samples[-1][1]
    t1, d1 = track_samples[idx - 1]
    t2, d2 = track_samples[idx]
    dt_total = (t2 - t1).total_seconds()
    if dt_total <= 0:
        return d1
    return d1 + (d2 - d1) * (target_dt - t1).total_seconds() / dt_total



def interpolate_altitude(samples, target_dt):
    if target_dt.tzinfo is not None:    
        target_dt = target_dt.replace(tzinfo=None)
    samples = [(dt.replace(tzinfo=None), s) for dt, s in samples]

    if not samples:
        return 0.0
    times = [dt for dt, _ in samples]
    idx = bisect_left(times, target_dt)
    if idx <= 0:
        if samples and target_dt < samples[0][0]:
            return samples[0][1]
        return samples[0][1] if samples else 0.0
    if idx >= len(samples):
        return samples[-1][1]
    t1, s1 = samples[idx - 1]
    t2, s2 = samples[idx]
    dt_total = (t2 - t1).total_seconds()
    if dt_total <= 0:
        return s1
    return s1 + (s2 - s1) * (target_dt - t1).total_seconds() / dt_total


def interpolate_iso(samples, target_dt):
    if target_dt.tzinfo is not None:
        target_dt = target_dt.replace(tzinfo=None)
    samples = [(dt.replace(tzinfo=None), v) for dt, v in samples]

    if not samples:
        return 0

    times = [dt for dt, _ in samples]
    idx = bisect_left(times, target_dt)

    if idx <= 0:
        return samples[0][1]
    if idx >= len(samples):
        return samples[-1][1]

    return samples[idx - 1][1]


def interpolate_exposure(samples, target_dt):
    if target_dt.tzinfo is not None:
        target_dt = target_dt.replace(tzinfo=None)
    samples = [(dt.replace(tzinfo=None), v) for dt, v in samples]

    if not samples:
        return 0

    times = [dt for dt, _ in samples]
    idx = bisect_left(times, target_dt)

    if idx <= 0:
        return samples[0][1]
    if idx >= len(samples):
        return samples[-1][1]

    return samples[idx - 1][1]


def interpolate_temperature(samples, target_dt):
    if target_dt.tzinfo is not None:
        target_dt = target_dt.replace(tzinfo=None)
    samples = [(dt.replace(tzinfo=None), v) for dt, v in samples]

    if not samples:
        return 0

    times = [dt for dt, _ in samples]
    idx = bisect_left(times, target_dt)

    if idx <= 0:
        return samples[0][1]
    if idx >= len(samples):
        return samples[-1][1]

    return samples[idx - 1][1]


def interpolate_value(samples, target_dt):
    """Generic step interpolation for scalar values (power, atemp, hr, cad)."""
    if not samples:
        return 0
    if target_dt.tzinfo is not None:
        target_dt = target_dt.replace(tzinfo=None)
    samples = [(dt.replace(tzinfo=None), v) for dt, v in samples]
    times = [dt for dt, _ in samples]
    idx = bisect_left(times, target_dt)
    if idx <= 0:
        return samples[0][1]
    if idx >= len(samples):
        return samples[-1][1]
    return samples[idx - 1][1]


def normalize_layout(layout_path, video_width, video_height):
    layout = default_layout(video_width, video_height)
    if layout_path and Path(layout_path).exists():
        user = json.loads(Path(layout_path).read_text(encoding='utf-8'))
        if "indicators" in user:
            layout["global"].update(user.get("global", {}))
            layout["smoothing"].update(user.get("smoothing", {}))
            for k, v in user.get("indicators", {}).items():
                if k in layout["indicators"] and isinstance(v, dict):
                    layout["indicators"][k].update(v)
        if "custom_texts" in user:
            layout["custom_texts"] = user["custom_texts"]
        
        if user.get("version", 0) < 5:
            # Prosta migracja z v4
            old_inds = layout.get("indicators", {})
            if "gauge" in old_inds:
                layout["indicators"]["speed_visual"] = old_inds["gauge"]
                layout["indicators"]["speed_visual"]["form"] = "gauge"
                layout["indicators"]["speed_visual"]["size"] = old_inds["gauge"].get("radius", 0.1)
                layout["indicators"]["speed_visual"]["thickness"] = old_inds["gauge"].get("arc_width", 0.007)
                layout["indicators"]["speed_visual"]["max_val"] = old_inds["gauge"].get("gauge_max", 60)
                layout["indicators"]["speed_visual"]["ticks"] = 6
            if "speed_text" in old_inds:
                layout["indicators"]["speed_text"]["form"] = "text"
                layout["indicators"]["speed_text"]["font_size"] = old_inds["speed_text"].get("font_speed", 0.04)
            if "distance_block" in old_inds:
                db = old_inds["distance_block"]
                layout["indicators"]["dist_visual"] = db.copy()
                layout["indicators"]["dist_visual"]["form"] = "bar"
                layout["indicators"]["dist_visual"]["size"] = db.get("bar_width", 0.2)
                layout["indicators"]["dist_visual"]["thickness"] = db.get("bar_height", 0.004)
                layout["indicators"]["dist_text"] = db.copy()
                layout["indicators"]["dist_text"]["form"] = "text"
                layout["indicators"]["dist_text"]["font_size"] = db.get("font_value", 0.017)
            layout["version"] = 5
            
    return layout


def resolve_font_path(family_name):
    """Znajduje ścieżkę pliku czcionki dla podanej nazwy rodziny (Windows)."""
    if os.name != 'nt':
        return family_name
    if Path(family_name).exists():
        return family_name
    try:
        import winreg
        fonts_dir = Path(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts')
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts') as key:
            count = winreg.QueryInfoKey(key)[1]
            for i in range(count):
                name, value, _ = winreg.EnumValue(key, i)
                if name.lower().startswith(family_name.lower()) and '(TrueType)' in name:
                    candidate = fonts_dir / value
                    if candidate.exists():
                        return str(candidate)
    except Exception:
        pass
    for ext in ('.ttf', '.otf'):
        candidate = Path(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts') / f'{family_name}{ext}'
        if candidate.exists():
            return str(candidate)
    return family_name


def load_font(font_path, size):
    key = (str(font_path), int(size))
    font = FONT_CACHE.get(key)
    if font is not None:
        return font
    try:
        font = ImageFont.truetype(str(font_path), size=int(size))
    except Exception:
        font = ImageFont.load_default()
    FONT_CACHE[key] = font
    return font


def format_raw_value(key, raw):
    if raw is None:
        return ""
    txt = str(raw).strip()
    if "Binary data" in txt:
        return ""
    if key.endswith("ExposureTimes"):
        return txt.split()[0]
    if key.endswith("ISOSpeeds"):
        return txt.split()[0]
    if key.endswith("CameraTemperature"):
        parts = txt.split()
        try:
            return f"{int(round(float(parts[0])))} C"
        except Exception:
            return txt
    parts = txt.split()
    if len(parts) > 1 and all(p.replace('.', '', 1).replace('-', '', 1).isdigit() for p in parts[:3]):
        return parts[0]
    return txt


def render_custom_text(canvas_w, canvas_h, font_path, cfg):
    """Renderuje pojedynczy custom text.
    cfg: dict z kluczami: enabled, text, x, y, rotation, font_size, color
    Zwraca (overlay_img, px_x, px_y) lub (None, 0, 0) jeśli wyłączony."""
    if not cfg.get("enabled", True):
        return None, 0, 0
    text = str(cfg.get("text", ""))
    if not text:
        return None, 0, 0
    min_dim = min(canvas_w, canvas_h)
    font_size_px = max(8, int(round(cfg.get("font_size", 0.03) * min_dim)))
    font = load_font(font_path, font_size_px)
    color_hex = cfg.get("color", "#FFFFFF")
    rgb = parse_hex_color(color_hex)
    if rgb is None:
        rgb = (255, 255, 255)
    fill_color = (rgb[0], rgb[1], rgb[2], 255)
    tmp = Image.new('RGBA', (canvas_w, font_size_px * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tmp)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    overlay = Image.new('RGBA', (tw + 8, th + 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.text((4, 4), text, font=font, fill=fill_color,
              stroke_width=2, stroke_fill=(0, 0, 0, 200))
    px = int(round(cfg.get("x", 0.5) * canvas_w))
    py = int(round(cfg.get("y", 0.5) * canvas_h))
    return overlay, px, py


def rotated_paste(base_img, overlay, center_x, center_y, rotation):
    rotation = int(rotation) % 360
    if rotation == 90:
        overlay = overlay.transpose(Image.Transpose.ROTATE_90)
    elif rotation == 180:
        overlay = overlay.transpose(Image.Transpose.ROTATE_180)
    elif rotation == 270:
        overlay = overlay.transpose(Image.Transpose.ROTATE_270)
    x = int(round(center_x - overlay.width / 2))
    y = int(round(center_y - overlay.height / 2))
    base_img.alpha_composite(overlay, (x, y))

def render_time_block(canvas_w, canvas_h, layout, font_path, date_text, time_text):
    cfg = layout["indicators"]["time_block"]
    if not cfg.get("enabled", True):
        return None, 0, 0

    min_dim = min(canvas_w, canvas_h)
    outline = int(layout["global"].get("text_outline", 3))

    label_px = max(12, s(cfg["font_label"], min_dim))
    date_px  = max(14, s(cfg["font_date"], min_dim))
    time_px  = max(14, s(cfg["font_time"], min_dim))

    font_label = load_font(font_path, label_px)
    font_date = load_font(font_path, date_px)
    font_time = load_font(font_path, time_px)

    tmp = Image.new('RGBA', (max(200, s(0.25, canvas_w)), max(100, s(0.12, canvas_h))), (0,0,0,0))
    draw = ImageDraw.Draw(tmp)

    y = 0
    draw.text((0, y), cfg.get('label', 'Czas'),
              font=font_label, fill=(210,210,210,255),
              stroke_width=outline, stroke_fill=(0,0,0,255))
    y += int(label_px * 1.3)

    draw.text((0, y), date_text,
              font=font_date, fill=(255,255,255,255),
              stroke_width=outline, stroke_fill=(0,0,0,255))
    y += int(date_px * 1.2)

    draw.text((0, y), time_text,
              font=font_time, fill=(255,255,255,255),
              stroke_width=outline, stroke_fill=(0,0,0,255))

    bbox = tmp.getbbox()
    if not bbox:
        return None, 0, 0

    return tmp.crop(bbox), s(cfg["x"], canvas_w), s(cfg["y"], canvas_h)


def render_value_indicator(canvas_w, canvas_h, layout, font_path, key, value, unit, label,
                           cfg_override=None, formatted_val=None, max_distance_m=None,
                           history_data=None, current_position=None):
    cfg = cfg_override if cfg_override else layout["indicators"].get(key)
    if not cfg or not cfg.get("enabled", True):
        return None, 0, 0, None

    form = cfg.get("form", "text")
    # Mapowanie dla kompatybilności wstecznej (stare layouty z polskimi nazwami)
    _FORM_MAP = {"TEXT": "text", "SUWAK": "bar", "LICZNIK": "text"}
    form = _FORM_MAP.get(form, form)
    min_dim = min(canvas_w, canvas_h)
    outline = int(layout["global"].get("text_outline", 3))
    fs = max(8, s(cfg.get("font_size", 0.02), min_dim))
    font = load_font(font_path, fs)

    val_min = float(cfg.get("min_val", 0))
    val_max = float(cfg.get("max_val", 100))
    ticks = int(cfg.get("ticks", 0))
    thickness = max(1, s(cfg.get("thickness", 0.005), min_dim))
    size_px = s(cfg.get("size", 0.1), min_dim if form == "gauge" else canvas_w)

    if form == "text":
        v_str = formatted_val if formatted_val else f"{value:.1f} {unit}"
        txt = f"{label}: {v_str}" if label else f"{v_str}"
        tmp = Image.new('RGBA', (canvas_w, fs * 3), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tmp)
        draw.text(
            (0, 0), txt, font=font,
            fill=(255, 255, 255, 255),
            stroke_width=outline, stroke_fill=(0, 0, 0, 255)
        )
        bbox = tmp.getbbox()
        if not bbox:
            return None, 0, 0, None
        cropped = tmp.crop(bbox)
        return cropped, s(cfg["x"], canvas_w), s(cfg["y"], canvas_h), None

    elif form == "bar":
        w, h = size_px, max(24, thickness * 6)
        img = Image.new('RGBA', (w + 40, h + 30), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        v_str = f"{value:.1f} {unit}"
        show_value = cfg.get("show_value", True)

        if label:
            draw.text(
                (20, 0), label,
                font=font,
                fill=(210, 210, 210, 255),
                stroke_width=outline,
                stroke_fill=(0, 0, 0, 255)
            )

        by = h - thickness - 5
        x1, x2 = 20, w + 20
        draw.line((x1, by, x2, by), fill=(160, 160, 160, 180), width=thickness)

        if ticks > 1:
            for i in range(ticks + 1):
                xt = x1 + (w * i / ticks)
                draw.line(
                    (xt, by - thickness, xt, by + thickness),
                    fill=(245, 245, 245, 220),
                    width=max(1, thickness // 4)
                )

        frac = max(0, min(1, (value - val_min) / (val_max - val_min))) if val_max > val_min else 0
        dot_x = x1 + frac * w
        dot_y = by

        draw.ellipse(
            (dot_x - thickness, dot_y - thickness, dot_x + thickness, dot_y + thickness),
            fill=(255, 50, 50, 255),
            outline=(255, 255, 255, 255)
        )
        extra = {
            "show_value": show_value,
            "value_text": v_str,
            "dot_x": dot_x,
            "dot_y": dot_y,
            "bar_w": w,
            "bar_h": h,
            "x1": x1,
            "x2": x2,
            "by": by,
            "show_range_labels": key == "dist_visual" and cfg.get("show_range_labels", False),
            "left_text": "0 km",
            "right_text": f"{max_distance_m/1000.0:.1f} km" if max_distance_m is not None else "",
        }
        return img, s(cfg["x"], canvas_w), s(cfg["y"], canvas_h), extra

    elif form == "gauge":
        # --- NOWA LOGIKA SKALI --- LICZNIK
        display_min = 0
        display_max = math.ceil(val_max / 10.0) * 10 if val_max > 0 else 10
        
        # Obliczamy ile będzie dziesiątek (czyli ile głównych kresek)
        major_ticks_count = int(display_max / 10)
        if major_ticks_count < 1: 
            major_ticks_count = 1
            
        sub_ticks_count = 10  # 10 mniejszych kresek między każdą "dziesiątką" (1 kreska = 1 km/h)
        total_ticks = major_ticks_count * sub_ticks_count
        # -------------------------

        radius = size_px
        img_size = int(radius * 2.4)
        img = Image.new('RGBA', (img_size, img_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx = cy = img_size // 2
        
        start_deg, end_deg = 180, 360
        
        for i in range(total_ticks + 1):
            a = math.radians(start_deg + (end_deg - start_deg) * i / total_ticks)
            cos_a = math.cos(a)
            sin_a = math.sin(a)
            
            if i % sub_ticks_count == 0:
                tick_len = thickness
                tick_width = max(3, int(thickness // 3))
                
                # Używamy display_max i display_min
                tick_val = display_min + (display_max - display_min) * (i / total_ticks)
                txt_tick = f"{tick_val:.0f}"  # .0f usunie miejsca po przecinku (będzie "10" zamiast "10.0")
                
                text_radius = radius - tick_len - (radius * 0.20)
                tx = cx + cos_a * text_radius
                ty = cy + sin_a * text_radius
                
                draw.text(
                    (tx, ty), txt_tick, font=font, 
                    fill=(255, 255, 255, 240), stroke_width=1, stroke_fill=(0, 0, 0, 255),
                    anchor="mm"
                )
            elif i % (sub_ticks_count // 2) == 0:
                tick_len = thickness * 0.7
                tick_width = max(2, int(thickness // 4))
            else:
                tick_len = thickness * 0.4
                tick_width = max(1, int(thickness // 6))
            
            r_out = radius
            r_in = radius - tick_len
            draw.line(
                (cx + cos_a * r_in, cy + sin_a * r_in,
                 cx + cos_a * r_out, cy + sin_a * r_out),
                fill=(240, 240, 240, 255), width=tick_width
            )

        # Wskazówka musi się opierać o nową, zaokrągloną skalę
        frac = max(0, min(1, (value - display_min) / (display_max - display_min))) if display_max > display_min else 0
        ang = math.radians(start_deg + (end_deg - start_deg) * frac)
        
        needle_r_out = radius + max(2, int(radius * 0.05))
        needle_r_in = radius - thickness - (radius * 0.40)
        
        draw.line(
            (cx + math.cos(ang) * needle_r_in, cy + math.sin(ang) * needle_r_in,
             cx + math.cos(ang) * needle_r_out, cy + math.sin(ang) * needle_r_out),
            fill=(220, 50, 50, 255), width=max(4, int(thickness // 2))
        )

        if key == 'speed_visual':
            if label:
                tw = draw.textbbox((0, 0), label, font=font)[2]
                draw.text(
                    (cx - tw // 2, cy + radius // 2), label,
                    font=font,
                    fill=(255, 255, 255, 255),
                    stroke_width=outline, stroke_fill=(0, 0, 0, 255)
                )
        else:
            txt_main = f"{value:.1f}"
            tw = draw.textbbox((0, 0), txt_main, font=font)[2]
            draw.text(
                (cx - tw // 2, cy + radius // 2), txt_main,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=outline, stroke_fill=(0, 0, 0, 255)
            )

        # --- CIEŃ (DROP SHADOW) ---
        shadow_offset = max(2, int(radius * 0.025))
        shadow = Image.new('RGBA', img.size, (0, 0, 0, 0))
        shadow.paste(img, (shadow_offset, shadow_offset))
        alpha = shadow.split()[3].point(lambda x: int(x * 0.35))
        alpha = alpha.filter(ImageFilter.GaussianBlur(radius=max(1, int(radius * 0.035))))
        shadow_rgba = Image.new('RGBA', img.size, (0, 0, 0, 0))
        shadow_rgba.putalpha(alpha)
        img = Image.alpha_composite(shadow_rgba, img)
        # -------------------------

        return img, s(cfg["x"], canvas_w), s(cfg["y"], canvas_h), None

    elif form == "chart":
        # Wykres (chart) - rysuje pełną historię danych z pionową linią kursora
        # history_data może być listą wartości lub dict {'values': [...], 'time_labels': [...]}
        time_labels = None
        chart_vals = None
        if isinstance(history_data, dict):
            chart_vals = history_data.get('values', [])
            time_labels = history_data.get('time_labels', None)
        elif isinstance(history_data, list):
            chart_vals = history_data
        
        if not chart_vals or len(chart_vals) < 2:
            chart_vals = [value, value]
        
        # Określamy indeks kursora na podstawie current_position (0.0-1.0)
        ci = None
        if current_position is not None:
            ci = int(round(current_position * (len(chart_vals) - 1)))
            ci = max(0, min(len(chart_vals) - 1, ci))
        
        # Wymiary wykresu
        chart_w = size_px
        chart_h = max(40, int(chart_w * 0.4))  # proporcja 2.5:1
        
        # Kolor linii – z configa (chart_color) lub domyślny wg typu
        custom_color = parse_hex_color(cfg.get('chart_color', ''))
        if custom_color:
            line_clr = custom_color
        elif 'speed' in key or 'cad' in key:
            line_clr = (255, 50, 50)
        elif 'alt' in key:
            line_clr = (50, 200, 50)
        elif 'dist' in key:
            line_clr = (50, 150, 255)
        elif 'power' in key:
            line_clr = (255, 200, 50)
        elif 'hr' in key:
            line_clr = (255, 50, 150)
        else:
            line_clr = (200, 200, 200)
        
        # Kolor wypełnienia i przezroczystość z configa
        chart_fill_alpha = int(cfg.get('fill_alpha', 40))
        chart_fill_color = parse_hex_color(cfg.get('fill_color', ''))
        
        chart_img = generate_history_chart(
            chart_vals, chart_w, chart_h,
            line_color=line_clr,
            line_thickness=max(2, thickness),
            fill_alpha=chart_fill_alpha,
            fill_color=chart_fill_color,
            current_index=ci,
            cursor_color=(255, 255, 255),
            show_axes=True,
            time_labels=time_labels,
        )
        
        # Dodajemy etykietę i wartość nad wykresem
        margin_top = fs + 8 if label else 0
        final_h = chart_h + margin_top + 4
        final_img = Image.new('RGBA', (chart_w + 8, final_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(final_img)
        
        if label:
            draw.text((4, 0), label, font=font,
                      fill=(210, 210, 210, 255),
                      stroke_width=outline, stroke_fill=(0, 0, 0, 255))
        
        # Wklejamy wykres pod etykietą
        final_img.paste(chart_img, (4, margin_top), chart_img)
        
        # Dodajemy wartość w prawym górnym rogu
        v_str = formatted_val if formatted_val else f"{value:.1f} {unit}"
        bbox = draw.textbbox((0, 0), v_str, font=font)
        vw = bbox[2] - bbox[0]
        draw.text((chart_w - vw, 0), v_str, font=font,
                  fill=(255, 255, 255, 255),
                  stroke_width=outline, stroke_fill=(0, 0, 0, 255))
        
        return final_img, s(cfg["x"], canvas_w), s(cfg["y"], canvas_h), None

    return None, 0, 0, None

def compose_overlay(canvas_w, canvas_h, layout, font_path, date_text, time_text, speed_value, distance_m, max_distance_m=None, alt_value=0.0, min_alt=None, max_alt=None, iso_value=None, exposure_value=None, temp_value=None, indicator_values=None, max_speed_kmh=None, power_value=None, atemp_value=None, hr_value=None, cad_value=None, _bboxes=None, chart_data=None, current_position=None):
    """indicator_values: opcjonalny dict {key: value} nadpisujący wartości dla poszczególnych wskaźników.
       Pozwala na użycie różnych źródeł danych (gpmf/gpx) dla różnych wskaźników.
       _bboxes: opcjonalny dict, do którego zapisywane są bounding boxy wskaźników {key: (x,y,w,h)}.
       chart_data: opcjonalny dict {key: [lista wartości]} dla wskaźników z form="chart".
       current_position: float 0.0-1.0, aktualna pozycja w filmie."""
    img = Image.new('RGBA', (canvas_w, canvas_h), (0, 0, 0, 0))
    if _bboxes is None:
        _bboxes = {}

    tb, tbx, tby = render_time_block(canvas_w, canvas_h, layout, font_path, date_text, time_text)
    if tb:
        tb_rotation = layout["indicators"]["time_block"].get("rotation", 0)
        rotated_paste(
            img,
            tb,
            tbx + tb.width // 2,
            tby + tb.height // 2,
            tb_rotation
        )
        if tb_rotation == 90:
            _bboxes['time_block'] = (int(tbx - tb.height // 2), int(tby - tb.width // 2), tb.height, tb.width)
        else:
            _bboxes['time_block'] = (int(tbx - tb.width // 2), int(tby - tb.height // 2), tb.width, tb.height)

    if indicator_values is None:
        indicator_values = {}

    for key, default_value, unit, default_label in [
        ("speed_visual", speed_value, "km/h", ""),
        ("speed_text", speed_value, "km/h", ""),
        ("dist_visual", distance_m / 1000.0, "km", ""),
        ("dist_text", distance_m / 1000.0, "km", ""),
        ("alt_visual", alt_value, "m", "Alt"),
        ("alt_text", alt_value, "m", "Alt"),
        ("iso_text", iso_value if iso_value is not None else 0, "ISO", "ISO"),
        ("exposure_text", exposure_value if exposure_value is not None else 0, "", "Exp"),
        ("temp_text", temp_value if temp_value is not None else 0, "C", "Temp"),
        ("power_text", power_value if power_value is not None else 0, "W", "Moc"),
        ("atemp_text", atemp_value if atemp_value is not None else 0, "°C", "ATemp"),
        ("hr_text", hr_value if hr_value is not None else 0, "BPM", "HR"),
        ("cad_text", cad_value if cad_value is not None else 0, "RPM", "Cad"),
    ]:
        if key in indicator_values:
            raw = indicator_values[key]
            # indicator_values przechowuje dystans w metrach, a domyślna wartość i
            # wyświetlanie oczekują km – konwertujemy
            if key in ("dist_visual", "dist_text"):
                value = raw / 1000.0
            else:
                value = raw
        else:
            value = default_value
        current_cfg = layout["indicators"][key].copy()

        if key == "dist_visual" and max_distance_m is not None:
            current_cfg["max_val"] = max(current_cfg["min_val"] + 0.001, max_distance_m / 1000.0)

        if key == "speed_visual" and max_speed_kmh is not None:
            rounded = math.ceil(max_speed_kmh / 10.0) * 10
            current_cfg["max_val"] = max(current_cfg.get("min_val", 0) + 0.001, rounded)

        if key in ("alt_visual", "alt_text") and min_alt is not None and max_alt is not None:
            current_cfg["min_val"] = min_alt
            current_cfg["max_val"] = max(min_alt + 1.0, max_alt)

        label = current_cfg.get("label", default_label)

        if key == "iso_text":
            formatted_val = f"{int(value)}"
        elif key == "exposure_text":
            formatted_val = f"1/{int(value)}" if value and int(value) > 0 else ""
        elif key == "temp_text":
            formatted_val = f"{int(value)}°C"
        elif key == "power_text":
            formatted_val = f"{int(value)}W"
        elif key == "atemp_text":
            formatted_val = f"{int(value)}°C"
        elif key == "hr_text":
            formatted_val = f"{int(value)} BPM"
        elif key == "cad_text":
            formatted_val = f"{int(value)} RPM"
        else:
            formatted_val = None
        # Przygotuj dane wykresu dla form="chart"
        chart_vals = None
        if chart_data and key in chart_data:
            chart_vals = chart_data[key]
        res, rx, ry, extra = render_value_indicator(
            canvas_w, canvas_h, layout, font_path, key, value, unit, label,
            cfg_override=current_cfg, formatted_val=formatted_val, max_distance_m=max_distance_m,
            history_data=chart_vals, current_position=current_position
        )

        if res:
            rotation = layout["indicators"][key].get("rotation", 0)
            # Lewe kotwiczenie dla form="text" – (x,y) to lewy-górny róg, nie środek
            if layout["indicators"][key].get("form", "text") == "text":
                if rotation == 90:
                    rx = rx + res.height // 2
                else:
                    rx = rx + res.width // 2
            rotated_paste(img, res, rx, ry, rotation)

            # Store bounding box for clickable preview
            if rotation == 90:
                _bboxes[key] = (int(ry - res.height // 2), int(rx - res.width // 2), res.height, res.width)
            elif rotation == 180:
                _bboxes[key] = (int(rx - res.width // 2), int(ry - res.height // 2), res.width, res.height)
            elif rotation == 270:
                _bboxes[key] = (int(ry - res.width // 2), int(rx - res.height // 2), res.height, res.width)
            else:
                _bboxes[key] = (int(rx - res.width // 2), int(ry - res.height // 2), res.width, res.height)

            draw = ImageDraw.Draw(img)
            cfg = current_cfg
            fs = max(10, int(s(cfg["font_size"], canvas_h)))
            font = load_font(font_path, fs)
            outline = max(1, fs // 12)

            # Pomijamy wyświetlanie wartości na pasku dist_visual (duplikuje dist_text)
            if extra and extra.get("show_value") and key != "dist_visual":
                text = extra["value_text"]
                bbox = draw.textbbox((0, 0), text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]

                ox = int(round(cfg.get("value_offset_x", 0.0) * canvas_w))
                oy = int(round(cfg.get("value_offset_y", 0.0) * canvas_h))

                if rotation == 90:
                    text_x = int(rx + res.height + 8 + ox)
                    text_y = int(ry + res.width / 2 - text_h / 2 + oy)
                else:
                    text_x = int(rx + extra["dot_x"] - text_w / 2 + ox)
                    text_y = int(ry + extra["dot_y"] - text_h - 8 + oy)

                draw.text(
                    (text_x, text_y), text, font=font,
                    fill=(255, 255, 255, 255),
                    stroke_width=outline, stroke_fill=(0, 0, 0, 255)
                )

            if key in ("dist_visual", "alt_visual") and cfg.get("show_range_labels", False):
                if key == "dist_visual":
                    left_text = "0 km"
                    right_text = f"{max_distance_m/1000.0:.1f} km" if max_distance_m is not None else ""
                else:
                    left_text = f"{int(cfg.get('min_val', 0))} m"
                    right_text = f"{int(cfg.get('max_val', 500))} m"

                rox = int(round(cfg.get("range_label_offset_x", 0.0) * canvas_w))
                roy = int(round(cfg.get("range_label_offset_y", 0.0) * canvas_h))
                rspreadx = int(round(cfg.get("range_label_spread_x", 0.0) * canvas_w))

                left_bbox = draw.textbbox((0, 0), left_text, font=font)
                left_w = left_bbox[2] - left_bbox[0]
                left_h = left_bbox[3] - left_bbox[1]

                if right_text:
                    right_bbox = draw.textbbox((0, 0), right_text, font=font)
                    right_w = right_bbox[2] - right_bbox[0]
                    right_h = right_bbox[3] - right_bbox[1]
                else:
                    right_w = right_h = 0

                if rotation == 90:
                    left_x = int(rx - left_w - 8 + rox)
                    left_y = int(ry + res.width - left_h / 2 + roy)

                    draw.text(
                        (left_x, left_y), left_text, font=font,
                        fill=(220, 220, 220, 255),
                        stroke_width=outline, stroke_fill=(0, 0, 0, 255)
                    )

                    if right_text:
                        # range_label_spread_x (rspreadx) oddala prawą etykietę od lewej
                        right_x = int(rx - right_w - 8 + rox)
                        right_y = int(ry - right_h / 2 + roy - rspreadx)

                        draw.text(
                            (right_x, right_y), right_text, font=font,
                            fill=(220, 220, 220, 255),
                            stroke_width=outline, stroke_fill=(0, 0, 0, 255)
                        )
                else:
                    left_y = int(ry + extra["by"] + 4 + roy)

                    draw.text(
                        (int(rx + extra["x1"] + rox), left_y), left_text, font=font,
                        fill=(220, 220, 220, 255),
                        stroke_width=outline, stroke_fill=(0, 0, 0, 255)
                    )

                    if right_text:
                        # range_label_spread_x (rspreadx) oddala prawą etykietę od lewej
                        draw.text(
                            (int(rx + extra["x2"] - right_w + rox + rspreadx), left_y), right_text, font=font,
                            fill=(220, 220, 220, 255),
                            stroke_width=outline, stroke_fill=(0, 0, 0, 255)
                        )

    # ── RENDER CUSTOM TEXTS ──
    custom_texts = layout.get("custom_texts", [])
    for ct_cfg in custom_texts:
        ct_res, ctx, cty = render_custom_text(canvas_w, canvas_h, font_path, ct_cfg)
        if ct_res:
            ct_rotation = int(ct_cfg.get("rotation", 0))
            rotated_paste(img, ct_res, ctx, cty, ct_rotation)

    return img

def render_preview(src_img, layout, font_path, date_text, time_text, speed_value, distance_m, max_distance_m, alt_value=0.0, min_alt=None, max_alt=None, iso_value=None, exposure_value=None, temp_value=None, indicator_values=None, max_speed_kmh=None, power_value=None, atemp_value=None, hr_value=None, cad_value=None, _bboxes=None, chart_data=None, current_position=None):
    img = src_img.convert('RGBA').copy()
    w, h = img.size
    if _bboxes is None:
        _bboxes = {}
    overlay = compose_overlay(w, h, layout, font_path, date_text, time_text, speed_value, distance_m, max_distance_m, alt_value, min_alt, max_alt, iso_value, exposure_value, temp_value, indicator_values=indicator_values, max_speed_kmh=max_speed_kmh, power_value=power_value, atemp_value=atemp_value, hr_value=hr_value, cad_value=cad_value, _bboxes=_bboxes, chart_data=chart_data, current_position=current_position)
    img.alpha_composite(overlay)
    return img


def init_worker(video_width, video_height, font_path, layout, field_samples, max_distance_m=None,
                iso_samples=None, exposure_samples=None, temperature_samples=None,
                gpx_speed_samples=None, gpx_track_samples=None, gpx_alt_samples=None,
                gpx_power_samples=None, gpx_atemp_samples=None, gpx_hr_samples=None, gpx_cad_samples=None,
                fit_speed_samples=None, fit_track_samples=None, fit_alt_samples=None,
                fit_power_samples=None, fit_atemp_samples=None, fit_hr_samples=None, fit_cad_samples=None,
                start_dt_utc=None, tz_offset_hours=None,
                speed_samples=None, track_samples=None, alt_samples=None,
                target_fps=None, update_rate_step=1, total_overlay_frames=None):
    WORKER_CACHE['video_width']          = video_width
    WORKER_CACHE['video_height']         = video_height
    WORKER_CACHE['font_path']            = font_path
    WORKER_CACHE['layout']               = layout
    WORKER_CACHE['field_samples']        = field_samples
    WORKER_CACHE['max_distance_m']       = max_distance_m or 1000.0
    WORKER_CACHE['iso_samples']          = iso_samples or []
    WORKER_CACHE['exposure_samples']     = exposure_samples or []
    WORKER_CACHE['temperature_samples']  = temperature_samples or []
    WORKER_CACHE['gpx_speed_samples']    = gpx_speed_samples or []
    WORKER_CACHE['gpx_track_samples']    = gpx_track_samples or []
    WORKER_CACHE['gpx_alt_samples']      = gpx_alt_samples or []
    WORKER_CACHE['gpx_power_samples']    = gpx_power_samples or []
    WORKER_CACHE['gpx_atemp_samples']    = gpx_atemp_samples or []
    WORKER_CACHE['gpx_hr_samples']       = gpx_hr_samples or []
    WORKER_CACHE['gpx_cad_samples']      = gpx_cad_samples or []
    WORKER_CACHE['fit_speed_samples']    = fit_speed_samples or []
    WORKER_CACHE['fit_track_samples']    = fit_track_samples or []
    WORKER_CACHE['fit_alt_samples']      = fit_alt_samples or []
    WORKER_CACHE['fit_power_samples']    = fit_power_samples or []
    WORKER_CACHE['fit_atemp_samples']    = fit_atemp_samples or []
    WORKER_CACHE['fit_hr_samples']       = fit_hr_samples or []
    WORKER_CACHE['fit_cad_samples']      = fit_cad_samples or []
    WORKER_CACHE['start_dt_utc']         = start_dt_utc
    WORKER_CACHE['tz_offset_hours']      = tz_offset_hours
    WORKER_CACHE['speed_samples']        = speed_samples or []
    WORKER_CACHE['track_samples']        = track_samples or []
    WORKER_CACHE['alt_samples']          = alt_samples or []
    WORKER_CACHE['target_fps']           = target_fps
    WORKER_CACHE['update_rate_step']     = update_rate_step
    WORKER_CACHE['total_overlay_frames'] = total_overlay_frames or 1


def _get_source_samples(source_type):
    """Pomocnicza funkcja dla workerów – zwraca (speed, track, alt) dla wskazanego źródła."""
    gpx_spd = WORKER_CACHE.get('gpx_speed_samples', [])
    gpx_trk = WORKER_CACHE.get('gpx_track_samples', [])
    gpx_alt = WORKER_CACHE.get('gpx_alt_samples', [])
    fit_spd = WORKER_CACHE.get('fit_speed_samples', [])
    fit_trk = WORKER_CACHE.get('fit_track_samples', [])
    fit_alt = WORKER_CACHE.get('fit_alt_samples', [])
    gpmf_spd = WORKER_CACHE.get('field_samples', {}).get('speed_samples', [])
    gpmf_trk = WORKER_CACHE.get('field_samples', {}).get('track_samples', [])
    gpmf_alt = WORKER_CACHE.get('field_samples', {}).get('alt_samples', [])
    if source_type == 'gpx':
        return (gpx_spd or gpmf_spd, gpx_trk or gpmf_trk, gpx_alt or gpmf_alt)
    if source_type == 'fit':
        return (fit_spd or gpmf_spd, fit_trk or gpmf_trk, fit_alt or gpmf_alt)
    return (gpmf_spd, gpmf_trk, gpmf_alt)


def _build_chart_data_worker(layout):
    """Buduje chart_data dla workerów z danych w WORKER_CACHE.
    Uwzględnia wszystkie wskaźniki: speed, dist, alt, power, hr, cad, atemp, iso, exposure, temp."""
    chart_data = {}
    for ind_key, ind_cfg in layout.get('indicators', {}).items():
        if ind_cfg.get('form') == 'chart' and ind_cfg.get('enabled', True):
            src = ind_cfg.get('source', 'gpmf')
            if 'speed' in ind_key:
                spd_s, _, _ = _get_source_samples(src)
                vals = [v for _, v in spd_s] if spd_s else []
            elif 'dist' in ind_key:
                _, trk_s, _ = _get_source_samples(src)
                vals = [v for _, v in trk_s] if trk_s else []
            elif 'alt' in ind_key:
                _, _, alt_s = _get_source_samples(src)
                vals = [v for _, v in alt_s] if alt_s else []
            elif 'power' in ind_key:
                vals = [v for _, v in (WORKER_CACHE.get('fit_power_samples') or WORKER_CACHE.get('gpx_power_samples') or [])]
            elif 'hr' in ind_key:
                vals = [v for _, v in (WORKER_CACHE.get('fit_hr_samples') or WORKER_CACHE.get('gpx_hr_samples') or [])]
            elif 'cad' in ind_key:
                vals = [v for _, v in (WORKER_CACHE.get('fit_cad_samples') or WORKER_CACHE.get('gpx_cad_samples') or [])]
            elif 'atemp' in ind_key:
                vals = [v for _, v in (WORKER_CACHE.get('fit_atemp_samples') or WORKER_CACHE.get('gpx_atemp_samples') or [])]
            elif 'iso' in ind_key:
                iso_s = WORKER_CACHE.get('iso_samples', [])
                vals = [v for _, v in iso_s] if iso_s else []
            elif 'exposure' in ind_key:
                exp_s = WORKER_CACHE.get('exposure_samples', [])
                vals = [v for _, v in exp_s] if exp_s else []
            elif 'temp' in ind_key and 'atemp' not in ind_key:
                temp_s = WORKER_CACHE.get('temperature_samples', [])
                vals = [v for _, v in temp_s] if temp_s else []
            else:
                vals = []  # brak fallbacku – pusta lista = brak wykresu
            if vals and len(vals) >= 2:
                chart_data[ind_key] = vals
    return chart_data


def render_overlay_job(job):
    if len(job) == 9:
        index, overlay_dir_text, start_dt_utc, tz_offset_hours, speed_samples, track_samples, alt_samples, target_fps, update_rate_step = job
    else:
        index, overlay_dir_text, start_dt_utc, tz_offset_hours, speed_samples, track_samples, alt_samples, target_fps = job
        update_rate_step = 1
    overlay_dir  = Path(overlay_dir_text)
    video_width  = WORKER_CACHE['video_width']
    video_height = WORKER_CACHE['video_height']
    font_path    = WORKER_CACHE['font_path']
    layout       = WORKER_CACHE['layout']
    field_samples  = WORKER_CACHE['field_samples']
    max_distance_m    = WORKER_CACHE.get('max_distance_m', 1000.0)
    iso_samples       = WORKER_CACHE.get('iso_samples', [])
    exposure_samples     = WORKER_CACHE.get('exposure_samples', [])
    temperature_samples  = WORKER_CACHE.get('temperature_samples', [])
    sample_t = (index * update_rate_step) / target_fps
    t0 = start_dt_utc if start_dt_utc is not None else speed_samples[0][0]
    current_dt_utc = t0 + timedelta(seconds=sample_t)

    current_dt_local = current_dt_utc + timedelta(hours=tz_offset_hours)

    # ── Oblicz wartości per-wskaźnik uwzględniając źródło danych ──
    indicator_values = {}
    for ind_key in ('speed_visual', 'speed_text', 'dist_visual', 'dist_text', 'alt_visual', 'alt_text'):
        ind_cfg = layout['indicators'].get(ind_key, {})
        src = ind_cfg.get('source', 'gpmf')
        gpx_spd = WORKER_CACHE.get('gpx_speed_samples', [])
        gpx_trk = WORKER_CACHE.get('gpx_track_samples', [])
        gpx_alt = WORKER_CACHE.get('gpx_alt_samples', [])
        fit_spd = WORKER_CACHE.get('fit_speed_samples', [])
        fit_trk = WORKER_CACHE.get('fit_track_samples', [])
        fit_alt = WORKER_CACHE.get('fit_alt_samples', [])
        if src == 'gpx':
            spd_s = gpx_spd or speed_samples
            trk_s = gpx_trk or track_samples
            alt_s = gpx_alt or alt_samples
        elif src == 'fit':
            spd_s = fit_spd or speed_samples
            trk_s = fit_trk or track_samples
            alt_s = fit_alt or alt_samples
        else:
            spd_s, trk_s, alt_s = speed_samples, track_samples, alt_samples
        if ind_key in ('speed_visual', 'speed_text'):
            indicator_values[ind_key] = interpolate_speed(spd_s, current_dt_utc)
        elif ind_key in ('dist_visual', 'dist_text'):
            indicator_values[ind_key] = interpolate_distance(trk_s, current_dt_utc)
        elif ind_key in ('alt_visual', 'alt_text'):
            indicator_values[ind_key] = interpolate_altitude(alt_s, current_dt_utc)

    iso_value       = interpolate_iso(iso_samples, current_dt_utc)
    exposure_value  = interpolate_exposure(exposure_samples, current_dt_utc)
    temp_value      = interpolate_temperature(temperature_samples, current_dt_utc)

    # ── Nowe wartości z GPX/FIT extensions ──
    gpx_power_samples = WORKER_CACHE.get('gpx_power_samples', [])
    gpx_atemp_samples = WORKER_CACHE.get('gpx_atemp_samples', [])
    gpx_hr_samples    = WORKER_CACHE.get('gpx_hr_samples', [])
    gpx_cad_samples   = WORKER_CACHE.get('gpx_cad_samples', [])
    fit_power_samples = WORKER_CACHE.get('fit_power_samples', [])
    fit_atemp_samples = WORKER_CACHE.get('fit_atemp_samples', [])
    fit_hr_samples    = WORKER_CACHE.get('fit_hr_samples', [])
    fit_cad_samples   = WORKER_CACHE.get('fit_cad_samples', [])
    power_value  = interpolate_value(fit_power_samples or gpx_power_samples, current_dt_utc)
    atemp_value  = interpolate_value(fit_atemp_samples or gpx_atemp_samples, current_dt_utc)
    hr_value     = interpolate_value(fit_hr_samples or gpx_hr_samples, current_dt_utc)
    cad_value    = interpolate_value(fit_cad_samples or gpx_cad_samples, current_dt_utc)

    # Użyj wartości z indicator_values z fallbackiem do głównych próbek
    speed_value = indicator_values.get('speed_visual', interpolate_speed(speed_samples, current_dt_utc))
    distance_m  = indicator_values.get('dist_visual', interpolate_distance(track_samples, current_dt_utc))
    alt_value   = indicator_values.get('alt_visual', interpolate_altitude(alt_samples, current_dt_utc))

    # max_distance_m z odpowiedniego źródła
    dist_src = layout['indicators'].get('dist_visual', {}).get('source', 'gpmf')
    if dist_src == 'gpx':
        gpx_trk = WORKER_CACHE.get('gpx_track_samples', [])
        if gpx_trk:
            max_distance_m = gpx_trk[-1][1]
    elif dist_src == 'fit':
        fit_trk = WORKER_CACHE.get('fit_track_samples', [])
        if fit_trk:
            max_distance_m = fit_trk[-1][1]

    # max_speed_kmh z odpowiedniego źródła – zaokrąglamy w górę do pełnej dziesiątki
    max_speed_kmh = None
    spd_src = layout['indicators'].get('speed_visual', {}).get('source', 'gpmf')
    if spd_src == 'gpx':
        gpx_spd_w = WORKER_CACHE.get('gpx_speed_samples', [])
        spd_for_range = gpx_spd_w or speed_samples
    elif spd_src == 'fit':
        fit_spd_w = WORKER_CACHE.get('fit_speed_samples', [])
        spd_for_range = fit_spd_w or speed_samples
    else:
        spd_for_range = speed_samples
    if spd_for_range:
        spd_vals = [s for _, s in spd_for_range]
        if spd_vals:
            max_speed_kmh = max(spd_vals)

    # Zakres alt z odpowiedniego źródła
    min_alt = None
    max_alt = None
    alt_src = layout['indicators'].get('alt_visual', {}).get('source', 'gpmf')
    if alt_src == 'gpx':
        gpx_alt_w = WORKER_CACHE.get('gpx_alt_samples', [])
        alt_for_range = gpx_alt_w or alt_samples
    elif alt_src == 'fit':
        fit_alt_w = WORKER_CACHE.get('fit_alt_samples', [])
        alt_for_range = fit_alt_w or alt_samples
    else:
        alt_for_range = alt_samples
    if alt_for_range:
        alts = [a for _, a in alt_for_range]
        if alts:
            min_alt = min(alts)
            max_alt = max(alts)

    date_text = current_dt_local.strftime('%Y-%m-%d')
    time_text = current_dt_local.strftime('%H:%M:%S')

    # ── Przygotuj dane wykresów (chart) ──
    total_frames = WORKER_CACHE.get('total_overlay_frames', 1)
    current_position = index / max(1, total_frames - 1) if total_frames > 1 else 0.0
    chart_data = _build_chart_data_worker(layout)

    img = compose_overlay(video_width, video_height, layout, font_path, date_text, time_text,
                          speed_value, distance_m, max_distance_m, alt_value,
                          min_alt, max_alt, iso_value, exposure_value, temp_value,
                          indicator_values=indicator_values, max_speed_kmh=max_speed_kmh,
                          power_value=power_value, atemp_value=atemp_value,
                          hr_value=hr_value, cad_value=cad_value,
                          chart_data=chart_data, current_position=current_position)
    img.save(overlay_dir / f'overlay_{index:06d}.bmp', format='BMP')
    return index


def generate_overlay_sequence(overlay_dir, duration_s, video_width, video_height, start_dt_utc, tz_offset_hours, speed_samples, track_samples, alt_samples, font_path, layout, field_samples, target_fps=30.0, workers=None, max_distance_m=None, progress_cb=None, cancel_event=None, update_rate_step=1, iso_samples=None, exposure_samples=None, temperature_samples=None, gpx_speed_samples=None, gpx_track_samples=None, gpx_alt_samples=None, gpx_power_samples=None, gpx_atemp_samples=None, gpx_hr_samples=None, gpx_cad_samples=None):
    overlay_dir.mkdir(parents=True, exist_ok=True)
    generation_fps = target_fps / update_rate_step
    total_overlay_frames = max(1, math.ceil(duration_s * generation_fps))
    if cancel_event is not None and cancel_event.is_set():
        return 0
    workers = workers or max(1, (os.cpu_count() or 1) - 1)
    jobs = [(i, str(overlay_dir), start_dt_utc, tz_offset_hours, speed_samples, track_samples, alt_samples, target_fps, update_rate_step) for i in range(total_overlay_frames)]
    start_time = time.time()

    WORKER_CACHE['total_overlay_frames'] = total_overlay_frames

    progress_interval = max(1, min(3, total_overlay_frames // 1000))
    if workers <= 1:
        init_worker(video_width, video_height, font_path, layout, field_samples, max_distance_m, iso_samples, exposure_samples, temperature_samples, gpx_speed_samples, gpx_track_samples, gpx_alt_samples, gpx_power_samples, gpx_atemp_samples, gpx_hr_samples, gpx_cad_samples, None, None, None, None, None, None, None, start_dt_utc, tz_offset_hours, speed_samples, track_samples, alt_samples, target_fps, update_rate_step)
        for i, job in enumerate(jobs, start=1):
            if cancel_event is not None and cancel_event.is_set():
                return i - 1
            render_overlay_job(job)
            if i % progress_interval == 0 or i == total_overlay_frames:
                elapsed = time.time() - start_time
                m, s = divmod(int(elapsed), 60)
                h, m = divmod(m, 60)
                elapse_str = f"{h:02d}:{m:02d}:{s:02d}"
                fps = i / elapsed if elapsed > 0 else 0
                stats = f"PNG: {i}/{total_overlay_frames} | fps: {fps:.1f} | elapse: {elapse_str}"
                if progress_cb: progress_cb(i, stats)
        return total_overlay_frames
    done = 0
    with ProcessPoolExecutor(max_workers=workers, initializer=init_worker,
                             initargs=(video_width, video_height, font_path, layout, field_samples, max_distance_m, iso_samples, exposure_samples, temperature_samples, gpx_speed_samples, gpx_track_samples, gpx_alt_samples, gpx_power_samples, gpx_atemp_samples, gpx_hr_samples, gpx_cad_samples, None, None, None, None, None, None, None, start_dt_utc, tz_offset_hours, speed_samples, track_samples, alt_samples, target_fps, update_rate_step)) as ex:
        chunk = max(1, total_overlay_frames // max(1, workers * 4))
        for _ in ex.map(render_overlay_job, jobs, chunksize=chunk):
            if cancel_event is not None and cancel_event.is_set():
                try:
                    ex.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
                break
            done += 1
            if done % progress_interval == 0 or done == total_overlay_frames:
                elapsed = time.time() - start_time
                m, s = divmod(int(elapsed), 60)
                h, m = divmod(m, 60)
                elapse_str = f"{h:02d}:{m:02d}:{s:02d}"
                fps = done / elapsed if elapsed > 0 else 0
                stats = f"PNG: {done}/{total_overlay_frames} | fps: {fps:.1f} | elapse: {elapse_str}"
                if progress_cb: progress_cb(done, stats)
        try:
            if cancel_event is not None and cancel_event.is_set():
                ex.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        return done


def build_overlay_video(ffmpeg_exe, overlay_dir, overlay_video_path, fps=30.0, total_frames=None, progress_cb=None, cancel_event=None, active_process_holder=None):
    cmd = [ffmpeg_exe, '-y', '-framerate', str(fps), '-i', str(overlay_dir / 'overlay_%06d.bmp'),
           '-c:v', 'qtrle', '-pix_fmt', 'argb', str(overlay_video_path)]
    if progress_cb and total_frames:
        run_ffmpeg_with_progress(cmd, total_frames, progress_cb, "MOV", cancel_event=cancel_event, active_process_holder=active_process_holder)
    else:
        if cancel_event is not None and cancel_event.is_set():
            return
        run_live(cmd)


# ─── NOWY PIPELINE: Producent-Konsument → pipe do FFmpeg ───────────────────

def _build_stream_ffmpeg_cmd(ffmpeg_exe, input_args, output_file,
                              overlay_w, overlay_h, generation_fps,
                              encoder, gpu, video_bitrate,
                              render_w, render_h, resolution_name,
                              container_rotation, rotation_degrees):
    """Zbuduj komendę ffmpeg dla streamingowego pipeline'u."""
    # Scale filter
    target_res = RESOLUTION_MAP.get(resolution_name)
    base_filter = (f'[0:v]scale={render_w}:{render_h}:flags=lanczos[base]'
                   if target_res else '[0:v]null[base]')

    if container_rotation == 180:
        # ffmpeg auto-rotuje strumień wejściowy — nakładka nałożona przed obrotem pasuje do oryg. wymiarów
        filter_complex = (
            f'{base_filter};'
            f'[1:v]setpts=PTS-STARTPTS,format=rgba[ov];'
            f'[base][ov]overlay=0:0:shortest=1[vtemp];'
            f'[vtemp]vflip,hflip[vout]'
        )
    elif container_rotation in (90, 270):
        filter_complex = (
            f'{base_filter};'
            f'[1:v]setpts=PTS-STARTPTS,format=rgba[ov];'
            f'[base][ov]overlay=0:0:shortest=1[vout]'
        )
    elif rotation_degrees == 180:
        filter_complex = (
            f'{base_filter};'
            f'[1:v]setpts=PTS-STARTPTS,format=rgba[ov];'
            f'[base][ov]overlay=0:0:shortest=1[vout]'
           
        )
    elif rotation_degrees == 90:
        filter_complex = (
            f'{base_filter};'
            f'[1:v]setpts=PTS-STARTPTS,format=rgba[ov];'
            f'[base][ov]overlay=0:0:shortest=1[vtemp];'
            f'[vtemp]transpose=1[vout]'
        )
    elif rotation_degrees == 270:
        filter_complex = (
            f'{base_filter};'
            f'[1:v]setpts=PTS-STARTPTS,format=rgba[ov];'
            f'[base][ov]overlay=0:0:shortest=1[vtemp];'
            f'[vtemp]transpose=2[vout]'
        )
    else:
        filter_complex = (
            f'{base_filter};'
            f'[1:v]setpts=PTS-STARTPTS,format=rgba[ov];'
            f'[base][ov]overlay=0:0:shortest=1[vout]'
        )

    cmd = [ffmpeg_exe, '-y'] + input_args + [
        '-f', 'image2pipe', '-c:v', 'png',
        '-r', str(generation_fps),
        '-i', 'pipe:0',
        '-filter_complex', filter_complex,
        '-map', '[vout]', '-map', '0:a?',
        '-map_metadata', '-1', '-metadata:s:v:0', 'rotate=0',
    ]

    if encoder == 'nv':
        cmd.extend(['-c:v', 'hevc_nvenc', '-preset', 'p4', '-tune', 'hq', '-rc', 'vbr',
                     '-cq', '24', '-pix_fmt', 'yuv420p', '-gpu', str(gpu), '-c:a', 'copy'])
    elif encoder == 'intel':
        cmd.extend(['-c:v', 'hevc_qsv', '-global_quality', '24', '-look_ahead', '1',
                     '-pix_fmt', 'nv12', '-c:a', 'copy'])
    else:
        cmd.extend(['-c:v', 'libx265', '-preset', 'medium', '-crf', '24',
                     '-pix_fmt', 'yuv420p', '-c:a', 'copy'])

    cmd = append_bitrate_args(cmd, encoder, video_bitrate)
    cmd.append(str(output_file))
    cmd.extend(['-progress', 'pipe:1', '-nostats', '-loglevel', 'error'])
    return cmd, filter_complex


def render_overlay_frame(index, start_dt_utc, tz_offset_hours, speed_samples, track_samples, alt_samples, target_fps, update_rate_step=1):
    """Render a single overlay frame – returns PIL Image RGBA. Uses WORKER_CACHE."""
    video_width  = WORKER_CACHE['video_width']
    video_height = WORKER_CACHE['video_height']
    font_path    = WORKER_CACHE['font_path']
    layout       = WORKER_CACHE['layout']
    field_samples  = WORKER_CACHE['field_samples']
    max_distance_m = WORKER_CACHE.get('max_distance_m', 1000.0)
    iso_samples       = WORKER_CACHE.get('iso_samples', [])
    exposure_samples  = WORKER_CACHE.get('exposure_samples', [])
    temperature_samples = WORKER_CACHE.get('temperature_samples', [])

    sample_t = (index * update_rate_step) / target_fps
    t0 = start_dt_utc if start_dt_utc is not None else speed_samples[0][0]
    current_dt_utc = t0 + timedelta(seconds=sample_t)
    current_dt_local = current_dt_utc + timedelta(hours=tz_offset_hours)

    # ── Wartości per-wskaźnik ──
    indicator_values = {}
    for ind_key in ('speed_visual', 'speed_text', 'dist_visual', 'dist_text', 'alt_visual', 'alt_text'):
        ind_cfg = layout['indicators'].get(ind_key, {})
        src = ind_cfg.get('source', 'gpmf')
        gpx_spd = WORKER_CACHE.get('gpx_speed_samples', [])
        gpx_trk = WORKER_CACHE.get('gpx_track_samples', [])
        gpx_alt = WORKER_CACHE.get('gpx_alt_samples', [])
        fit_spd = WORKER_CACHE.get('fit_speed_samples', [])
        fit_trk = WORKER_CACHE.get('fit_track_samples', [])
        fit_alt = WORKER_CACHE.get('fit_alt_samples', [])
        if src == 'gpx':
            spd_s = gpx_spd or speed_samples
            trk_s = gpx_trk or track_samples
            alt_s = gpx_alt or alt_samples
        elif src == 'fit':
            spd_s = fit_spd or speed_samples
            trk_s = fit_trk or track_samples
            alt_s = fit_alt or alt_samples
        else:
            spd_s, trk_s, alt_s = speed_samples, track_samples, alt_samples
        if ind_key in ('speed_visual', 'speed_text'):
            indicator_values[ind_key] = interpolate_speed(spd_s, current_dt_utc)
        elif ind_key in ('dist_visual', 'dist_text'):
            indicator_values[ind_key] = interpolate_distance(trk_s, current_dt_utc)
        elif ind_key in ('alt_visual', 'alt_text'):
            indicator_values[ind_key] = interpolate_altitude(alt_s, current_dt_utc)

    iso_value       = interpolate_iso(iso_samples, current_dt_utc)
    exposure_value  = interpolate_exposure(exposure_samples, current_dt_utc)
    temp_value      = interpolate_temperature(temperature_samples, current_dt_utc)

    power_value = interpolate_value(WORKER_CACHE.get('fit_power_samples', []) or WORKER_CACHE.get('gpx_power_samples', []), current_dt_utc)
    atemp_value = interpolate_value(WORKER_CACHE.get('fit_atemp_samples', []) or WORKER_CACHE.get('gpx_atemp_samples', []), current_dt_utc)
    hr_value    = interpolate_value(WORKER_CACHE.get('fit_hr_samples', []) or WORKER_CACHE.get('gpx_hr_samples', []), current_dt_utc)
    cad_value   = interpolate_value(WORKER_CACHE.get('fit_cad_samples', []) or WORKER_CACHE.get('gpx_cad_samples', []), current_dt_utc)

    speed_value = indicator_values.get('speed_visual', interpolate_speed(speed_samples, current_dt_utc))
    distance_m  = indicator_values.get('dist_visual', interpolate_distance(track_samples, current_dt_utc))
    alt_value   = indicator_values.get('alt_visual', interpolate_altitude(alt_samples, current_dt_utc))

    # max_distance_m per source
    dist_src = layout['indicators'].get('dist_visual', {}).get('source', 'gpmf')
    if dist_src == 'gpx':
        gpx_trk = WORKER_CACHE.get('gpx_track_samples', [])
        if gpx_trk:
            max_distance_m = gpx_trk[-1][1]
    elif dist_src == 'fit':
        fit_trk = WORKER_CACHE.get('fit_track_samples', [])
        if fit_trk:
            max_distance_m = fit_trk[-1][1]

    # max_speed_kmh per source
    max_speed_kmh = None
    spd_src = layout['indicators'].get('speed_visual', {}).get('source', 'gpmf')
    if spd_src == 'gpx':
        gpx_spd_w = WORKER_CACHE.get('gpx_speed_samples', [])
        spd_for_range = gpx_spd_w or speed_samples
    elif spd_src == 'fit':
        fit_spd_w = WORKER_CACHE.get('fit_speed_samples', [])
        spd_for_range = fit_spd_w or speed_samples
    else:
        spd_for_range = speed_samples
    if spd_for_range:
        spd_vals = [s for _, s in spd_for_range]
        if spd_vals:
            max_speed_kmh = max(spd_vals)

    # altitude range per source
    min_alt = None
    max_alt = None
    alt_src = layout['indicators'].get('alt_visual', {}).get('source', 'gpmf')
    if alt_src == 'gpx':
        gpx_alt_w = WORKER_CACHE.get('gpx_alt_samples', [])
        alt_for_range = gpx_alt_w or alt_samples
    elif alt_src == 'fit':
        fit_alt_w = WORKER_CACHE.get('fit_alt_samples', [])
        alt_for_range = fit_alt_w or alt_samples
    else:
        alt_for_range = alt_samples
    if alt_for_range:
        alts = [a for _, a in alt_for_range]
        if alts:
            min_alt = min(alts)
            max_alt = max(alts)

    date_text = current_dt_local.strftime('%Y-%m-%d')
    time_text = current_dt_local.strftime('%H:%M:%S')

    # ── Przygotuj dane wykresów (chart) ──
    total_frames = WORKER_CACHE.get('total_overlay_frames', 1)
    current_position = index / max(1, total_frames - 1) if total_frames > 1 else 0.0
    chart_data = _build_chart_data_worker(layout)

    return compose_overlay(video_width, video_height, layout, font_path, date_text, time_text,
                           speed_value, distance_m, max_distance_m, alt_value,
                           min_alt, max_alt, iso_value, exposure_value, temp_value,
                           indicator_values=indicator_values, max_speed_kmh=max_speed_kmh,
                           power_value=power_value, atemp_value=atemp_value,
                           hr_value=hr_value, cad_value=cad_value,
                           chart_data=chart_data, current_position=current_position)


def render_frame_bytes_job(job):
    """Multiprocessing worker: render one overlay frame, return (index, png_bytes)."""
    index = job[0]  # job = (index,) — wszystko inne z WORKER_CACHE
    start_dt_utc = WORKER_CACHE.get('start_dt_utc')
    tz_offset_hours = WORKER_CACHE.get('tz_offset_hours')
    speed_samples = WORKER_CACHE.get('speed_samples')
    track_samples = WORKER_CACHE.get('track_samples')
    alt_samples = WORKER_CACHE.get('alt_samples')
    target_fps = WORKER_CACHE.get('target_fps')
    update_rate_step = WORKER_CACHE.get('update_rate_step', 1)
    img = render_overlay_frame(index, start_dt_utc, tz_offset_hours,
                                speed_samples, track_samples, alt_samples,
                                target_fps, update_rate_step)
    buf = io.BytesIO()
    img.save(buf, format='PNG', compress_level=1)
    return index, buf.getvalue()


def stream_overlay_to_ffmpeg(ffmpeg_exe, input_files, output_file,
                              duration_s, start_dt_utc, tz_offset_hours,
                              speed_samples, track_samples, alt_samples,
                              font_path, layout, field_samples,
                              target_fps=30.0, update_rate_step=1,
                              max_distance_m=None, workers=None,
                              iso_samples=None, exposure_samples=None, temperature_samples=None,
                              gpx_speed_samples=None, gpx_track_samples=None, gpx_alt_samples=None,
                              gpx_power_samples=None, gpx_atemp_samples=None, gpx_hr_samples=None, gpx_cad_samples=None,
                              fit_speed_samples=None, fit_track_samples=None, fit_alt_samples=None,
                              fit_power_samples=None, fit_atemp_samples=None, fit_hr_samples=None, fit_cad_samples=None,
                              progress_cb=None, cancel_event=None, active_process_holder=None,
                              encoder='nv', gpu=0, resolution_name='source', video_bitrate='',
                              rotation_degrees=0, container_rotation=0,
                              overlay_w=1920, overlay_h=1080,
                              render_w=1920, render_h=1080):
    """
    Producer-Consumer pipeline:
    - Producent: ProcessPoolExecutor renderuje klatki równolegle → (index, bytes)
    - Konsument: główny wątek odbiera, sortuje po indeksie, pipe'uje do FFmpeg
    """
    generation_fps = target_fps / update_rate_step
    total_overlay_frames = max(1, math.ceil(duration_s * generation_fps))

    # Init worker cache (wszystkie dane potrzebne workerom)
    init_worker(overlay_w, overlay_h, font_path, layout, field_samples, max_distance_m,
                iso_samples, exposure_samples, temperature_samples,
                gpx_speed_samples, gpx_track_samples, gpx_alt_samples,
                gpx_power_samples, gpx_atemp_samples, gpx_hr_samples, gpx_cad_samples,
                fit_speed_samples, fit_track_samples, fit_alt_samples,
                fit_power_samples, fit_atemp_samples, fit_hr_samples, fit_cad_samples,
                start_dt_utc, tz_offset_hours,
                speed_samples, track_samples, alt_samples,
                target_fps, update_rate_step, total_overlay_frames)

    if cancel_event is not None and cancel_event.is_set():
        return 0

    # ── Build FFmpeg input args ──
    input_args = []
    if isinstance(input_files, list) and len(input_files) > 1:
        concat_txt = Path(output_file).parent / "render_concat_list.txt"
        with open(concat_txt, 'w', encoding='utf-8') as f:
            for p in input_files:
                escaped_p = str(p.absolute()).replace("'", "'\\''")
                f.write(f"file '{escaped_p}'\n")
        input_args = ['-f', 'concat', '-safe', '0', '-i', str(concat_txt)]
    else:
        input_file = input_files[0] if isinstance(input_files, list) else input_files
        input_args = ['-autorotate', '-i', str(input_file)]

    # Build ffmpeg command
    cmd, filter_complex = _build_stream_ffmpeg_cmd(
        ffmpeg_exe, input_args, output_file,
        overlay_w, overlay_h, generation_fps,
        encoder, gpu, video_bitrate,
        render_w, render_h, resolution_name,
        container_rotation, rotation_degrees,
    )

    print('FFmpeg streaming cmd:', ' '.join(map(str, cmd)), flush=True)
    print(f"[STREAM] overlay={overlay_w}x{overlay_h}  render={render_w}x{render_h}  "
          f"gen_fps={generation_fps}  frames={total_overlay_frames}", flush=True)
    print(f"[STREAM] filter: {filter_complex}", flush=True)

    # ── Start FFmpeg ──
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True,
                               startupinfo=startupinfo)
    if active_process_holder is not None:
        active_process_holder['process'] = process

    start_time = time.time()
    total_piped = 0

    # ── Producent: wielordzeniowe renderowanie ──
    # Jobs: (index,) – reszta z WORKER_CACHE (init_worker + powyższe)
    jobs = [(i,) for i in range(total_overlay_frames)]

    workers = workers or max(1, (os.cpu_count() or 1) - 1)
    n_workers = min(workers, total_overlay_frames)

    try:
        if n_workers <= 1:
            # ── Single-threaded ──
            for i in range(total_overlay_frames):
                if cancel_event is not None and cancel_event.is_set():
                    break
                _, png_bytes = render_frame_bytes_job((i,))
                process.stdin.buffer.write(png_bytes)
                total_piped += 1
                if total_piped % 50 == 0 or total_piped == total_overlay_frames:
                    _report_stream_progress(total_piped, total_overlay_frames, start_time, progress_cb)
        else:
            # ── Multi-worker (Producer-Consumer) ──
            from concurrent.futures import as_completed
            with ProcessPoolExecutor(max_workers=n_workers, initializer=init_worker,
                                     initargs=(overlay_w, overlay_h, font_path, layout, field_samples, max_distance_m,
                                               iso_samples, exposure_samples, temperature_samples,
                                               gpx_speed_samples, gpx_track_samples, gpx_alt_samples,
                                               gpx_power_samples, gpx_atemp_samples, gpx_hr_samples, gpx_cad_samples,
                                               fit_speed_samples, fit_track_samples, fit_alt_samples,
                                               fit_power_samples, fit_atemp_samples, fit_hr_samples, fit_cad_samples,
                                               start_dt_utc, tz_offset_hours,
                                               speed_samples, track_samples, alt_samples,
                                               target_fps, update_rate_step, total_overlay_frames)) as ex:
                # Submit all jobs
                future_to_idx = {ex.submit(render_frame_bytes_job, job): i for i, job in enumerate(jobs)}

                # Reorder buffer
                reorder_buf = {}
                next_idx = 0

                for f in as_completed(future_to_idx):
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    idx, png_bytes = f.result()
                    reorder_buf[idx] = png_bytes

                    # Pipe in order
                    while next_idx in reorder_buf:
                        process.stdin.buffer.write(reorder_buf.pop(next_idx))
                        total_piped += 1
                        next_idx += 1
                        if total_piped % 50 == 0 or total_piped == total_overlay_frames:
                            _report_stream_progress(total_piped, total_overlay_frames, start_time, progress_cb)

                if cancel_event is not None and cancel_event.is_set():
                    for f in future_to_idx:
                        f.cancel()
                    ex.shutdown(wait=False, cancel_futures=True)

                # Flush remaining buffer
                while next_idx in reorder_buf:
                    process.stdin.buffer.write(reorder_buf.pop(next_idx))
                    total_piped += 1
                    next_idx += 1
                    _report_stream_progress(total_piped, total_overlay_frames, start_time, progress_cb)

        process.stdin.close()
    except BrokenPipeError:
        print("[STREAM] FFmpeg pipe closed unexpectedly.", flush=True)
    except Exception as e:
        print(f"[STREAM] Error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        try:
            process.terminate()
        except Exception:
            pass
        raise

    # ── Wait for FFmpeg ──
    remaining = []
    for line in process.stdout:
        remaining.append(line.strip())
    process.wait()

    if active_process_holder is not None:
        active_process_holder['process'] = None

    rc = process.returncode
    if rc != 0 and not (cancel_event is not None and cancel_event.is_set()):
        extra = "\n".join(remaining).strip()
        raise RuntimeError(f'FFmpeg failed with exit code {rc}\n{extra}')

    # Cleanup concat list
    if isinstance(input_files, list) and len(input_files) > 1:
        concat_txt = Path(output_file).parent / "render_concat_list.txt"
        if concat_txt.exists():
            concat_txt.unlink()

    return total_piped


def _report_stream_progress(done, total, start_time, progress_cb):
    elapsed = time.time() - start_time
    m, s = divmod(int(elapsed), 60)
    h, m = divmod(m, 60)
    fps = done / elapsed if elapsed > 0 else 0
    stats = f"Stream: {done}/{total} | fps: {fps:.1f} | elapse: {h:02d}:{m:02d}:{s:02d}"
    if progress_cb:
        progress_cb(done, stats)


def run_ffmpeg_with_progress(cmd, total_frames, progress_cb, msg_prefix, cancel_event=None, active_process_holder=None):
    cmd.extend(['-progress', 'pipe:1', '-nostats', '-loglevel', 'error'])
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, startupinfo=startupinfo)
    if active_process_holder is not None:
        active_process_holder['process'] = process

    frame, fps, out_time, speed = 0, "0", "00:00:00", "0x"
    start_time = time.time()
    other_output = []

    for line in process.stdout:
        if cancel_event is not None and cancel_event.is_set():
            try:
                process.terminate()
            except Exception:
                pass
            break
        key, _, val = line.partition('=')
        key, val = key.strip(), val.strip()
        if '=' not in line:
            # non progress output (errors, warnings) captured here
            other_output.append(line.strip())
            continue
        if key == 'frame':
            try: frame = min(int(val), total_frames)
            except: pass
        elif key == 'fps':
            fps = val
        elif key == 'out_time':
            out_time = val.split('.')[0]
        elif key == 'speed':
            speed = val
        elif key == 'progress':
            elapsed = int(time.time() - start_time)
            m, s = divmod(elapsed, 60)
            h, m = divmod(m, 60)
            elapse_str = f"{h:02d}:{m:02d}:{s:02d}"
            stats = f"{msg_prefix}: {frame}/{total_frames} | fps: {fps} | speed: {speed} | time: {out_time} | elapse: {elapse_str}"
            if progress_cb: progress_cb(frame, stats)
    process.wait()
    rc = process.returncode
    if rc != 0:
        extra = "\n".join(other_output).strip()
        raise RuntimeError(f'FFmpeg process failed with exit code {rc}\n{extra}')
    if active_process_holder is not None:
        active_process_holder['process'] = None


def scale_filter_for_resolution(resolution_name):
    target = RESOLUTION_MAP.get(resolution_name)
    if not target:
        return '[0:v]null[base]'
    w, h = target
    return f'[0:v]scale={w}:{h}:flags=lanczos[base]'


def append_bitrate_args(cmd, encoder, video_bitrate):
    if not video_bitrate:
        return cmd
    if encoder == 'nv':
        cmd.extend(['-b:v', video_bitrate, '-maxrate', video_bitrate])
        bufsize = video_bitrate
        try:
            if video_bitrate.lower().endswith('m'):
                bufsize = f'{float(video_bitrate[:-1]) * 2:g}M'
            elif video_bitrate.lower().endswith('k'):
                bufsize = f'{float(video_bitrate[:-1]) * 2:g}k'
        except Exception:
            pass
        cmd.extend(['-bufsize', bufsize])
    else:
        cmd.extend(['-b:v', video_bitrate])
    return cmd


def get_rotation_from_metadata(records):
    for rec in ensure_records_list(records):
        if not isinstance(rec, dict):
            continue
        flat = flatten_record(rec)
        if "Main:AutoRotation" in flat:
            ar = flat["Main:AutoRotation"]
            if ar:
                ar_lower = str(ar).lower().strip()
                if ar_lower == "down":   return 180
                elif ar_lower == "up":   return 0
                elif ar_lower == "left": return 270
                elif ar_lower == "right":return 90
        if "Main:Rotation" in flat:
            try:
                return int(float(str(flat["Main:Rotation"]).strip())) % 360
            except (ValueError, TypeError):
                pass
    return 0


def get_container_rotation(ffprobe_exe, video_path):
    """
    Odczytaj tag 'rotate' z kontenera MP4 — stosowany automatycznie przez ffmpeg przy odczycie.
    Jeśli zwraca != 0, ffmpeg auto-rotuje i NIE potrzebujemy ręcznego transpose w filter_complex.
    """
    if isinstance(video_path, list):
        video_path = video_path[0] if video_path else None
    if video_path is None:
        return 0
    try:
        out = run([
            ffprobe_exe, '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream_tags=rotate:stream_side_data=rotation',
            '-of', 'json', str(video_path)
        ])
        data = json.loads(out)
        streams = data.get('streams', [])
        if streams:
            rotate_tag = streams[0].get('tags', {}).get('rotate', None)
            if rotate_tag is not None:
                return int(float(str(rotate_tag))) % 360
            for sd in streams[0].get('side_data_list', []):
                rot = sd.get('rotation', None)
                if rot is not None:
                    return abs(int(float(str(rot)))) % 360
    except Exception:
        pass
    return 0


def apply_overlay_video(ffmpeg_exe, input_files, overlay_video, output_file, encoder, gpu,
                        target_fps, resolution_name='source', video_bitrate='',
                        rotation_degrees=0, container_rotation=0, total_frames=None, progress_cb=None,
                        cancel_event=None, active_process_holder=None):
    """
    Strategia rotacji v4.1.3:
    - container_rotation != 0: ffmpeg auto-rotuje przy odczycie (domyślne zachowanie).
      Nakładka PNG jest generowana w oryginalnych wymiarach z ffprobe (przed obrotem).
      Po auto-rotacji ffmpega obraz jest poprawnie zorientowany — NIE dodajemy transpose.
    - container_rotation == 0 ale rotation_degrees != 0: obracamy ręcznie po nałożeniu nakładki.
    """
    base_chain = scale_filter_for_resolution(resolution_name)
    
    input_args = []
    if isinstance(input_files, list) and len(input_files) > 1:
        # Tworzenie pliku listy dla demuxera concat
        concat_txt = Path(output_file).parent / "render_concat_list.txt"
        with open(concat_txt, 'w', encoding='utf-8') as f:
            for p in input_files:
                escaped_p = str(p.absolute()).replace("'", "'\\''")
                f.write(f"file '{escaped_p}'\n")
        input_args = ['-f', 'concat', '-safe', '0', '-i', str(concat_txt)]
    else:
        input_file = input_files[0] if isinstance(input_files, list) else input_files
        input_args = ['-autorotate', '-i', str(input_file)]

    # Przy renderingu używamy tej samej domyślnej orientacji FFmpeg, co w podglądzie.
    # Dodatkowo: gdy kontener zawiera rotację 180°, odwracamy nakładkę o 180° przed nałożeniem.
    if container_rotation == 180:
        # ffmpeg auto-rotuje strumień wejściowy — nakładka nałożona przed obrotem pasuje do oryg. wymiarów
        filter_complex = (
            f'{base_chain};[1:v]fps={target_fps}[ov];[base][ov]overlay=0:0:shortest=1[vout]'
        )
    elif container_rotation in (90, 270):
        # ffmpeg auto-rotuje strumień wejściowy — nakładka nałożona przed obrotem pasuje do oryg. wymiarów
        filter_complex = f'{base_chain};[1:v]fps={target_fps}[ov];[base][ov]overlay=0:0:shortest=1[vout]'
    elif rotation_degrees == 180:
        filter_complex = f'{base_chain};[1:v]fps={target_fps}[ov];[base][ov]overlay=0:0:shortest=1[vtemp];[vtemp]vflip,hflip[vout]'
    elif rotation_degrees == 90:
        filter_complex = f'{base_chain};[1:v]fps={target_fps}[ov];[base][ov]overlay=0:0:shortest=1[vtemp];[vtemp]transpose=1[vout]'
    elif rotation_degrees == 270:
        filter_complex = f'{base_chain};[1:v]fps={target_fps}[ov];[base][ov]overlay=0:0:shortest=1[vtemp];[vtemp]transpose=2[vout]'
    else:
        filter_complex = f'{base_chain};[1:v]fps={target_fps}[ov];[base][ov]overlay=0:0:shortest=1[vout]'

    cmd = [ffmpeg_exe, '-y'] + input_args + ['-i', str(overlay_video),
           '-filter_complex', filter_complex, '-map', '[vout]', '-map', '0:a?',
           '-map_metadata', '-1', '-metadata:s:v:0', 'rotate=0']
    try:
        print('FFmpeg final command:', shlex.join(cmd), flush=True)
    except Exception:
        print('FFmpeg final command:', ' '.join(map(str, cmd)), flush=True)

    if encoder == 'nv':
        cmd.extend([
            '-c:v', 'hevc_nvenc', '-preset', 'p4', '-tune', 'hq', '-rc', 'vbr',
            '-cq', '24', '-pix_fmt', 'yuv420p', '-gpu', str(gpu), '-c:a', 'copy'
        ])
    elif encoder == 'intel':
        cmd.extend([
            '-c:v', 'hevc_qsv', '-global_quality', '24', '-look_ahead', '1',
            '-pix_fmt', 'nv12', '-c:a', 'copy'
        ])
    else:
        cmd.extend([
            '-c:v', 'libx265', '-preset', 'medium', '-crf', '24',
            '-pix_fmt', 'yuv420p', '-c:a', 'copy'
        ])

    cmd = append_bitrate_args(cmd, encoder, video_bitrate)
    cmd.append(str(output_file))
    if progress_cb and total_frames:
        run_ffmpeg_with_progress(cmd, total_frames, progress_cb, "Render", cancel_event=cancel_event, active_process_holder=active_process_holder)
    else:
        if cancel_event is not None and cancel_event.is_set():
            return
        run_live(cmd)

    if isinstance(input_files, list) and len(input_files) > 1:
        concat_txt = Path(output_file).parent / "render_concat_list.txt"
        if concat_txt.exists(): concat_txt.unlink()

def extract_samples_exiftool(flat):
    samples = []

    # znajdź wszystkie prefixy: Doc1, Doc1-1 itd.
    prefixes = set()
    for key in flat.keys():
        if key.startswith("Doc") and ":GPSDateTime" in key:
            prefixes.add(key.split(":")[0])

    for prefix in sorted(prefixes):
        dt_str = flat.get(f"{prefix}:GPSDateTime")
        speed = flat.get(f"{prefix}:GPSSpeed")

        if not dt_str:
            continue

        # parsowanie czasu
        try:

            dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S.%f")
            # ✅ KLUCZOWE (usuń timezone całkowicie)
            dt = dt.replace(tzinfo=None)

        except:
            try:
                dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
            except:
                continue

        # parsowanie prędkości
        try:
            speed = float(speed) if speed is not None else 0.0
        except:
            speed = 0.0

        samples.append((dt, speed))

    samples.sort(key=lambda x: x[0])

    print("✅ EXIF samples:", len(samples))

    return samples

def extract_altitude_samples_exiftool(flat):
    samples = []
    prefixes = set()
    for key in flat.keys():
        if key.startswith("Doc") and ":GPSDateTime" in key:
            prefixes.add(key.split(":")[0])

    for prefix in sorted(prefixes):
        dt_str = flat.get(f"{prefix}:GPSDateTime")
        alt_str = flat.get(f"{prefix}:GPSAltitude")

        if not dt_str:
            continue

        try:
            dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S.%f")
            dt = dt.replace(tzinfo=None)
        except:
            try:
                dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                dt = dt.replace(tzinfo=None)
            except:
                continue

        alt = parse_float_maybe(alt_str)
        if alt is None:
            alt = 0.0

        samples.append((dt, alt))

    samples.sort(key=lambda x: x[0])
    return samples

# ─── GUI ────────────────────────────────────────────────────────────────────

class ScrollableFrame(tk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.vbar   = tk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.vbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.inner     = tk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', self._on_frame_configure)
        self.canvas.bind('<Configure>', self._on_canvas_configure)

    def _on_frame_configure(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))



    def _on_canvas_configure(self, event=None):
        self.canvas.itemconfigure(self.window_id, width=event.width)
    #Szerokość panelu lewego


class NumericRow(tk.Frame):
    def __init__(self, master, name, value, mn, mx, step, callback, is_int=False):
        super().__init__(master)
        self.mn, self.mx, self.step = mn, mx, step
        self.callback = callback
        self.is_int = is_int
        self.var = tk.DoubleVar(value=float(value))

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0)

        tk.Label(self, text=FIELD_LABELS.get(name, name), width=12, anchor='w').grid(row=0, column=0, sticky='w', padx=(0, 4))

        self.scale = tk.Scale(
            self,
            variable=self.var,
            from_=mn,
            to=mx,
            resolution=step,
            orient=tk.HORIZONTAL,
            showvalue=False,
            length=85,
            sliderlength=12,
            width=10,
            takefocus=1,
            command=lambda _=None: self.on_scale()
        )
        self.scale.grid(row=0, column=1, sticky='ew', padx=(0, 4))
        self.scale.bind('<Button-1>', lambda e: self.scale.focus_set())
        self.entry = tk.Entry(self, width=5)
        self.entry.grid(row=0, column=2, sticky='e')
        self.entry.insert(0, self.format_value(value))
        self.entry.bind('<Return>', self.on_entry)
        self.entry.bind('<FocusOut>', self.on_entry)

    def format_value(self, value):
        if self.is_int or self.step >= 1:
            return str(int(round(value)))
        return f'{value:.3f}'.rstrip('0').rstrip('.')

    def clamp(self, value):
        return max(self.mn, min(self.mx, value))

    def on_scale(self):
        self.sync_entry()
        self.callback()

    def on_entry(self, event=None):
        try:
            value = float(self.entry.get().replace(',', '.'))
        except ValueError:
            self.sync_entry()
            return
        self.var.set(self.clamp(value))
        self.sync_entry()
        self.callback()

    def sync_entry(self):
        self.entry.delete(0, tk.END)
        self.entry.insert(0, self.format_value(self.var.get()))

    def _on_arrow_left(self, event=None):
        self.var.set(self.clamp(self.var.get() - self.step))
        self.sync_entry()
        self.callback()

    def _on_arrow_right(self, event=None):
        self.var.set(self.clamp(self.var.get() + self.step))
        self.sync_entry()
        self.callback()

    def get(self):
        return int(round(self.var.get())) if self.is_int else float(self.var.get())

class BoolRow(tk.Frame):
    def __init__(self, master, name, value, callback):
        super().__init__(master)
        self.var = tk.BooleanVar(value=bool(value))
        tk.Checkbutton(self, text=FIELD_LABELS.get(name, name), variable=self.var, command=callback).pack(anchor='w')

    def get(self):
        return bool(self.var.get())


class ChoiceRow(tk.Frame):
    def __init__(self, master, name, value, choices, callback):
        super().__init__(master)
        self.callback = callback

        tk.Label(self, text=FIELD_LABELS.get(name, name), width=8, anchor='w').pack(side=tk.LEFT)

        self.var = tk.StringVar(value=str(value))

        cb = ttk.Combobox(
            self,
            textvariable=self.var,
            values=[str(c) for c in choices],
            state='readonly',
            width=3
        )
        cb.pack(side=tk.LEFT, padx=(2, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self.callback())

    def get(self):
        val = self.var.get()
        try:
            return int(val)
        except Exception:
            return val


class TextRow(tk.Frame):
    def __init__(self, master, name, value, callback):
        super().__init__(master)
        self.callback = callback
        tk.Label(self, text=FIELD_LABELS.get(name, name), width=12, anchor='w').pack(side=tk.LEFT)
        self.var = tk.StringVar(value=str(value))
        e = tk.Entry(self, textvariable=self.var)
        e.pack(side=tk.LEFT)
        e.bind('<Return>',   lambda e: self.callback())
        e.bind('<FocusOut>', lambda e: self.callback())

    def get(self):
        return self.var.get()


class ColorRow(tk.Frame):
    """Widget wyboru koloru z podglądem i przyciskiem otwierającym paletę kolorów."""
    def __init__(self, master, name, value, callback):
        super().__init__(master)
        self.callback = callback
        tk.Label(self, text=FIELD_LABELS.get(name, name), width=12, anchor='w').pack(side=tk.LEFT)
        self.var = tk.StringVar(value=str(value) if value else "#FF3232")
        # Podgląd koloru
        self.color_preview = tk.Canvas(self, width=24, height=18, highlightthickness=1,
                                        highlightbackground='#555')
        self.color_preview.pack(side=tk.LEFT, padx=(2, 4))
        self._update_preview()
        # Przycisk palety
        btn = tk.Button(self, text="Paleta...", command=self._choose_color)
        btn.pack(side=tk.LEFT)

    def _update_preview(self):
        try:
            color = self.var.get().strip()
            if not color.startswith('#'):
                color = '#' + color
            self.color_preview.configure(bg=color)
        except Exception:
            self.color_preview.configure(bg='#888888')

    def _choose_color(self):
        from tkinter import colorchooser
        initial = self.var.get().strip()
        if not initial.startswith('#'):
            initial = '#' + initial
        result = colorchooser.askcolor(color=initial, title="Wybierz kolor wykresu")
        if result and result[1]:
            self.var.set(result[1])
            self._update_preview()
            self.callback()

    def get(self):
        val = self.var.get().strip()
        if val and not val.startswith('#'):
            val = '#' + val
        return val


class HudTunerApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f'HUD Tuner v{APP_VERSION}')
        self.root.geometry('1600x1050')
        self.base_dir      = Path(__file__).resolve().parent
        self.video_paths_to_process = []
        self.font_path     = resolve_font_path('Arial')
        self.video_path    = None
        self.meta_path     = None
        self.gpx_path      = None   # manually selected or auto-discovered GPX
        self.fit_path      = None   # manually selected or auto-discovered FIT
        self.records       = []
        # Dane z GPMF (GoPro)
        self.speed_samples = []
        self.alt_samples      = []
        self.track_samples    = []
        # Dane z GPX (nadpisują GPMF dla wybranych wskaźników)
        self.gpx_speed_samples = []
        self.gpx_alt_samples   = []
        self.gpx_track_samples = []
        self.gpx_power_samples = []
        self.gpx_atemp_samples = []
        self.gpx_hr_samples = []
        self.gpx_cad_samples = []
        # Dane z FIT (nadpisują GPMF dla wybranych wskaźników)
        self.fit_speed_samples = []
        self.fit_alt_samples   = []
        self.fit_track_samples = []
        self.fit_power_samples = []
        self.fit_atemp_samples = []
        self.fit_hr_samples = []
        self.fit_cad_samples = []
        self.iso_samples      = []
        self.exposure_samples    = []
        self.temperature_samples = []
        self.start_dt_utc  = None
        self.src_img       = Image.new('RGB', (1280, 720), (0, 0, 0))
        self.last_preview_timestamp = -1
        self.layout        = default_layout(*self.src_img.size)
        self.photo         = None
        self.ffprobe_path  = find_local_tool(self.base_dir, ['ffprobe.exe', 'ffprobe']) or 'ffprobe'
        self.exiftool_path = find_local_tool(self.base_dir, ['exiftool.exe', 'exiftool']) or 'exiftool'
        self.ffmpeg_exe = None
        self.ffprobe_exe = None
        self.video_duration_s = 0.0
        self._refresh_after_id = None
        self.render_cancel_event = threading.Event()
        self._active_process = None
        self._render_executor = None
        self.render_button = None
        self.indicator_bboxes = {}  # {key: (x,y,w,h)} in original image coords, for clickable preview

        self.encoder_var         = tk.StringVar(value='nv')
        self.rotation_var        = tk.StringVar(value='auto')
        self.resolution_var      = tk.StringVar(value='source')
        self.update_rate_var     = tk.StringVar(value='Full')
        self.fps                 = 30.0
        self.worker_mode_var     = tk.StringVar(value='auto')
        self.worker_count_var    = tk.StringVar(value='8')
        self.tz_offset_var       = tk.StringVar(value='2')
        self.font_style_var      = tk.StringVar(value='Arial')
        self.outline_var         = tk.IntVar(value=3)
        self.seek_var            = tk.DoubleVar(value=0.0)
        self.output_var          = tk.StringVar(value='output_h265.mp4')
        self.video_bitrate_var   = tk.StringVar(value='40M')
        self.video_info_var      = tk.StringVar(value='Brak pliku MP4')
        self.meta_info_var       = tk.StringVar(value='Telemetry JSON: nie wygenerowano')
        # Smoothing is always active with SMOOTHING_WINDOW = 5

        self.main_pw = tk.PanedWindow(root, orient=tk.HORIZONTAL, sashrelief=tk.RAISED)
        self.main_pw.pack(fill='both', expand=True)
        self.left_panel   = tk.Frame(self.main_pw)
        self.center_panel = tk.Frame(self.main_pw)
        self.right_panel  = tk.Frame(self.main_pw)
        self.main_pw.add(self.left_panel,   minsize=10, width=400)
        self.main_pw.add(self.center_panel, minsize=1000)
        self.main_pw.add(self.right_panel,  minsize=60, width=80)

        self.left_scroll = ScrollableFrame(self.left_panel)
        self.left_scroll.pack(fill='both', expand=True, padx=8, pady=8)
        left = self.left_scroll.inner

        top = tk.Frame(left)
        top.pack(fill=tk.X, pady=(0, 8))
        tk.Button(top, text='Wybierz MP4',    command=self.open_video).pack(anchor='w', pady=(6,0))
        tk.Label(top,  textvariable=self.video_info_var, justify='left', anchor='w').pack(fill=tk.X, pady=(6,0))
        tk.Label(top,  textvariable=self.meta_info_var,  justify='left', anchor='w').pack(fill=tk.X, pady=(6,0))
        tk.Button(top, text='Wczytaj GPX/FIT', command=self.open_telemetry).pack(anchor='w', pady=(4, 0))
        self.gpx_info_var = tk.StringVar(value='GPX: brak (auto-wykrywanie)')
        tk.Label(top, textvariable=self.gpx_info_var, justify='left', anchor='w',
                 fg='#0077cc').pack(fill=tk.X, pady=(2, 0))
        self.fit_info_var = tk.StringVar(value='FIT: brak')
        tk.Label(top, textvariable=self.fit_info_var, justify='left', anchor='w',
                 fg='#cc5500').pack(fill=tk.X, pady=(0, 4))
        tk.Button(top, text='Zapisz Konfigurację', command=self.save_configuration).pack(fill=tk.X, pady=(6,0))
        tk.Button(top, text='Wczytaj Konfiguracje',          command=self.load_json).pack(fill=tk.X, pady=(6,0))

        # ── Wybór czcionki HUD ──
        font_frame = tk.LabelFrame(left, text='Czcionka HUD')
        font_frame.pack(fill=tk.X, pady=(0, 8))
        fonts = sorted(tkfont.families())
        if 'Arial' not in fonts and fonts:
            self.font_style_var.set(fonts[0])
            self.font_path = resolve_font_path(fonts[0])
        self.font_combo = ttk.Combobox(font_frame, textvariable=self.font_style_var,
                                        values=fonts, state='readonly')
        self.font_combo.pack(fill=tk.X, padx=4, pady=4)
        self.font_combo.bind('<<ComboboxSelected>>', self.on_font_change)

        # ── Outline (obramowanie tekstu) ──
        outline_frame = tk.LabelFrame(left, text='Obramowanie (outline)')
        outline_frame.pack(fill=tk.X, pady=(0, 8))
        self.outline_var.set(self.layout.get("global", {}).get("text_outline", 3))
        tk.Scale(outline_frame, variable=self.outline_var, from_=0, to=10,
                 resolution=1, orient=tk.HORIZONTAL, length=200,
                 command=lambda _: self.on_outline_change()).pack(padx=4, pady=4)

        builtin_box = tk.LabelFrame(left, text='Wskaźniki Telemetrii')
        builtin_box.pack(fill=tk.X, pady=(0, 8))
        list_frame = tk.Frame(builtin_box)
        list_frame.pack(fill=tk.X)
        # Lewa lista – główne wskaźniki
        left_list_frame = tk.Frame(list_frame)
        left_list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(left_list_frame, text="Główne", font=('', 8, 'bold')).pack()
        self.indicator_list = tk.Listbox(left_list_frame, height=10, exportselection=False)
        for key in self.layout['indicators'].keys():
            if key not in GPX_EXT_FIELDS:
                self.indicator_list.insert(tk.END, key)
        self.indicator_list.pack(fill=tk.X)
        self.indicator_list.bind('<<ListboxSelect>>', self.on_builtin_select)
        self.indicator_list.selection_set(0)
        # Prawa lista – GPX extensions
        right_list_frame = tk.Frame(list_frame)
        right_list_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        tk.Label(right_list_frame, text="GPX Ext", font=('', 8, 'bold')).pack()
        self.gpx_ext_list = tk.Listbox(right_list_frame, height=10, exportselection=False)
        for key in GPX_EXT_FIELDS:
            self.gpx_ext_list.insert(tk.END, GPX_EXT_LABELS.get(key, key))
        self.gpx_ext_list.pack(fill=tk.X)
        self.gpx_ext_list.bind('<<ListboxSelect>>', self.on_gpx_ext_select)
        self.gpx_ext_list.selection_set(0)

        # ── Custom Texts sekcja ──
        custom_texts_box = tk.LabelFrame(left, text='Niestandardowe Teksty')
        custom_texts_box.pack(fill=tk.X, pady=(0, 8))
        ct_list_frame = tk.Frame(custom_texts_box)
        ct_list_frame.pack(fill=tk.X)
        self.custom_texts_list = tk.Listbox(ct_list_frame, height=5, exportselection=False)
        # Wypełnij listę nazwami custom_texts
        self._rebuild_custom_texts_list()
        self.custom_texts_list.pack(fill=tk.X)
        self.custom_texts_list.bind('<<ListboxSelect>>', self.on_custom_text_select)
        ct_btn_frame = tk.Frame(custom_texts_box)
        ct_btn_frame.pack(fill=tk.X, pady=(2, 0))
        tk.Button(ct_btn_frame, text='Dodaj tekst', command=self.add_custom_text).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(ct_btn_frame, text='Usuń', command=self.remove_custom_text).pack(side=tk.LEFT)

        props_box = tk.LabelFrame(left, text='Właściwości')
        props_box.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.props_scroll = ScrollableFrame(props_box)
        self.props_scroll.pack(fill='both', expand=True, ipady=150)
        self.props_container = self.props_scroll.inner
        self.property_widgets = {}
        self.edit_mode = 'builtin'

        center_pw = tk.PanedWindow(self.center_panel, orient=tk.VERTICAL, sashrelief=tk.RAISED)
        center_pw.pack(fill='both', expand=True, padx=8, pady=8)
        preview_wrap = tk.Frame(center_pw)
        center_pw.add(preview_wrap, minsize=480, height=550)
        self.preview_label = tk.Label(preview_wrap, bg='#222')
        self.preview_label.pack(fill=tk.BOTH, expand=True)
        self.preview_label.bind('<Configure>', self.on_preview_resize)
        self.preview_label.bind('<Button-1>', self.on_preview_click)

        self.loading_progress = ttk.Progressbar(preview_wrap, orient=tk.HORIZONTAL, mode='indeterminate')
        self.loading_progress.pack(fill=tk.X)

        seek_frame = tk.Frame(center_pw)
        center_pw.add(seek_frame, minsize=90)
        self.seek_slider = tk.Scale(seek_frame, variable=self.seek_var, from_=0, to=100,
                                   orient=tk.HORIZONTAL, showvalue=False, label="Czas wideo",
                                   resolution=1, tickinterval=0,
                                   command=lambda _: (self.schedule_refresh(100), self.update_seek_time_label()),
                                   takefocus=1)
        self.seek_slider.pack(fill=tk.X)
        self.seek_slider.bind('<Button-1>', lambda e: self.seek_slider.focus_set())
        for key in ('<Left>', '<Right>', '<Up>', '<Down>'):
            self.seek_slider.bind(key, self.on_seek_arrow)

        tick_canvas = tk.Canvas(seek_frame, height=20, highlightthickness=0, bg='#1e1e1e')
        tick_canvas.pack(fill=tk.X)
        tick_canvas.bind('<Configure>', lambda e: self.draw_tick_labels())
        self.tick_canvas = tick_canvas

        # ── Progress frame under seek bar ──
        progress_frame = tk.Frame(center_pw)
        center_pw.add(progress_frame, minsize=40, height=50)
        self.render_progress = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.render_progress.pack(fill=tk.X, pady=(4, 2), padx=8)
        self.render_stats = tk.Label(progress_frame, text="Gotowy", font=('Consolas', 8))
        self.render_stats.pack(fill=tk.X, pady=(0, 4), padx=8)

        render_box = tk.LabelFrame(self.right_panel, text='Render')
        render_box.pack(fill=tk.X, padx=8, pady=8)
        tk.Label(render_box, text='Encoder').pack(anchor='w')
        tk.OptionMenu(render_box, self.encoder_var, *ENCODER_OPTIONS).pack(fill=tk.X)
        tk.Label(render_box, text='Rotation').pack(anchor='w', pady=(6,0))
        rot_om = tk.OptionMenu(render_box, self.rotation_var, *ROTATION_OPTIONS, command=lambda _: self.refresh())
        rot_om.config(width=6)
        rot_om.pack(fill=tk.X)
        tk.Label(render_box, text='Resolution').pack(anchor='w', pady=(6,0))
        tk.OptionMenu(render_box, self.resolution_var, *RESOLUTION_OPTIONS).pack(fill=tk.X)
        tk.Label(render_box, text='Update rate').pack(anchor='w', pady=(6,0))
        update_rate_om = tk.OptionMenu(render_box, self.update_rate_var, 'Full', 'Half', 'Quarter', command=lambda _: self.refresh())
        update_rate_om.pack(fill=tk.X)
        tk.Label(render_box, text='Video bitrate').pack(anchor='w', pady=(6,0))
        tk.Entry(render_box, textvariable=self.video_bitrate_var).pack(fill=tk.X)
        tk.Label(render_box, text='TZ Offset (UTC)').pack(anchor='w', pady=(6,0))
        tk.Entry(render_box, textvariable=self.tz_offset_var).pack(fill=tk.X)
        tk.Label(render_box, text='Workers').pack(anchor='w', pady=(6,0))
        wm = tk.Frame(render_box)
        wm.pack(fill=tk.X)
        tk.Radiobutton(wm, text='Auto',    variable=self.worker_mode_var, value='auto').pack(side=tk.LEFT)
        tk.Radiobutton(wm, text='Ręcznie', variable=self.worker_mode_var, value='manual').pack(side=tk.LEFT)
        tk.Entry(render_box, textvariable=self.worker_count_var).pack(fill=tk.X)
        tk.Label(render_box, text='Output file').pack(anchor='w', pady=(6,0))
        tk.Entry(render_box, textvariable=self.output_var).pack(fill=tk.X)

        self.render_button = tk.Button(render_box, text='Eksport do mp4', command=self.render_now)
        self.render_button.pack(fill=tk.X, pady=(8,0))

        self.build_property_editor_builtin()
        self.root.after_idle(self.refresh)

    def schedule_refresh(self, delay=60):
        if self._refresh_after_id is not None:
            try:
                self.root.after_cancel(self._refresh_after_id)
            except Exception:
                pass
        self._refresh_after_id = self.root.after(delay, self.refresh)

    def on_preview_resize(self, event=None):
        self.schedule_refresh(60)

    def on_preview_click(self, event):
        """Handle click on preview label – find indicator under cursor and select it."""
        if not self.speed_samples or not self.indicator_bboxes:
            return
        pw = self.preview_label.winfo_width()
        ph = self.preview_label.winfo_height()
        src_w, src_h = self.src_img.size
        if pw < 10 or ph < 10 or src_w < 1 or src_h < 1:
            return
        # Map click coords back to original image coords
        scale = min(pw / src_w, ph / src_h)
        thumb_w = int(src_w * scale)
        thumb_h = int(src_h * scale)
        offset_x = (pw - thumb_w) // 2
        offset_y = (ph - thumb_h) // 2
        orig_x = (event.x - offset_x) / scale
        orig_y = (event.y - offset_y) / scale

        # Find indicator that contains the click point
        hit_key = None
        # Check in reverse order so top-most (last drawn) wins
        indicator_order = [
            'alt_text', 'alt_visual', 'dist_text', 'dist_visual',
            'speed_text', 'speed_visual', 'time_block',
            'temp_text', 'exposure_text', 'iso_text',
            'cad_text', 'hr_text', 'atemp_text', 'power_text',
        ]
        for key in indicator_order:
            bbox = self.indicator_bboxes.get(key)
            if bbox is None:
                continue
            x, y, w, h = bbox
            if x <= orig_x <= x + w and y <= orig_y <= y + h:
                hit_key = key
                break

        if hit_key is None:
            return

        # Select the indicator in the appropriate list
        if hit_key in GPX_EXT_FIELDS:
            # Find and select in gpx_ext_list
            gpx_names = [GPX_EXT_LABELS.get(k, k) for k in GPX_EXT_FIELDS]
            try:
                idx = GPX_EXT_FIELDS.index(hit_key)
                self.gpx_ext_list.selection_clear(0, tk.END)
                self.gpx_ext_list.selection_set(idx)
                self.gpx_ext_list.activate(idx)
                self.indicator_list.selection_clear(0, tk.END)
                self.build_property_editor_builtin()
            except (ValueError, tk.TclError):
                pass
        else:
            # Find and select in indicator_list
            all_builtin = [self.indicator_list.get(i) for i in range(self.indicator_list.size())]
            try:
                idx = all_builtin.index(hit_key)
                self.indicator_list.selection_clear(0, tk.END)
                self.indicator_list.selection_set(idx)
                self.indicator_list.activate(idx)
                self.gpx_ext_list.selection_clear(0, tk.END)
                self.build_property_editor_builtin()
            except (ValueError, tk.TclError):
                pass

    def format_time(self, total_sec):
        total_sec = int(total_sec)
        if total_sec >= 3600:
            return f"{total_sec // 3600}:{(total_sec % 3600) // 60:02d}:{total_sec % 60:02d}"
        else:
            return f"{total_sec // 60:02d}:{total_sec % 60:02d}"

    def update_seek_time_label(self):
        self.draw_tick_labels()

    def draw_tick_labels(self, event=None):
        """Rysuje podziałkę czasu na tick_canvas z etykietami w formacie MM:SS / HH:MM:SS."""
        c = self.tick_canvas
        c.delete('all')
        cw = c.winfo_width()
        if cw < 10:
            return
        total = int(self.seek_slider.cget('to'))
        if total <= 0:
            return
        # Wyznacz odstęp między tickami
        if total <= 60:
            step = 10
        elif total <= 600:
            step = 60
        elif total <= 3600:
            step = 300
        elif total <= 7200:
            step = 600
        else:
            step = 1800
        # Rysuj ticki i etykiety
        for t in range(0, total + 1, step):
            x = int(cw * t / total)
            c.create_line(x, 0, x, 8, fill='#aaaaaa', width=1)
            c.create_text(x, 10, text=self.format_time(t), anchor='n',
                          font=('Consolas', 8), fill='#ffffff')
        # ostatni tick (jeśli total nie jest wielokrotnością step)
        if total % step != 0:
            x = cw - 1
            c.create_line(x, 0, x, 8, fill='#aaaaaa', width=1)
            c.create_text(x, 10, text=self.format_time(total), anchor='n',
                          font=('Consolas', 8), fill='#ffffff')

    def on_seek_arrow(self, event=None):
        current = self.seek_var.get()
        if event.keysym in ('Left', 'Down'):
            new_value = max(0, current - 1)
        else:
            new_value = current + 1
        self.seek_var.set(new_value)
        self.schedule_refresh(100)
        return 'break'

    def build_property_editor_builtin(self):
        self.edit_mode = 'builtin'
        for child in self.props_container.winfo_children():
            child.destroy()
        self.property_widgets = {}
        # Sprawdź, która lista ma zaznaczenie
        ct_sel = self.custom_texts_list.curselection()
        if ct_sel:
            idx = ct_sel[0]
            custom_texts = self.layout.get("custom_texts", [])
            if 0 <= idx < len(custom_texts):
                cfg = custom_texts[idx]
                schema = [
                    ("enabled", "bool", None, None, None),
                    ("text", "text", None, None, None),
                    ("x", "float", 0.0, 1.0, 0.001),
                    ("y", "float", 0.0, 1.0, 0.001),
                    ("rotation", "choice", [0, 90, 180, 270], None, None),
                    ("font_size", "float", 0.005, 0.2, 0.001),
                    ("color", "color", None, None, None),
                ]
                for field_name, field_type, a, b, c in schema:
                    if field_type == 'bool':
                        row = BoolRow(self.props_container, field_name, cfg.get(field_name, True), self.on_builtin_change)
                    elif field_type == 'choice':
                        row = ChoiceRow(self.props_container, field_name, cfg.get(field_name, a[0]), a, self.on_builtin_change)
                    elif field_type == 'int':
                        row = NumericRow(self.props_container, field_name, cfg.get(field_name, a), a, b, c, self.on_builtin_change, is_int=True)
                    elif field_type == 'text':
                        row = TextRow(self.props_container, field_name, cfg.get(field_name, ''), self.on_builtin_change)
                    elif field_type == 'color':
                        row = ColorRow(self.props_container, field_name, cfg.get(field_name, '#FFFFFF'), self.on_builtin_change)
                    else:
                        row = NumericRow(self.props_container, field_name, cfg.get(field_name, a), a, b, c, self.on_builtin_change)
                    row.pack(fill=tk.X, padx=4, pady=2)
                    self.property_widgets[field_name] = row
            return
        sel = self.indicator_list.curselection()
        if sel:
            name = self.indicator_list.get(sel[0])
        else:
            sel = self.gpx_ext_list.curselection()
            if sel:
                name = GPX_EXT_FIELDS[sel[0]]
            else:
                name = list(self.layout['indicators'].keys())[0]
        cfg = self.layout['indicators'].get(name)
        if cfg is None:
            return
        schema = BUILTIN_FIELDS.get(name, get_value_schema())
        for field_name, field_type, a, b, c in schema:
            if field_type == 'bool':
                row = BoolRow(self.props_container, field_name, cfg.get(field_name, False), self.on_builtin_change)
            elif field_type == 'choice':
                row = ChoiceRow(self.props_container, field_name, cfg.get(field_name, a[0]), a, self.on_builtin_change)
            elif field_type == 'int':
                row = NumericRow(self.props_container, field_name, cfg.get(field_name, a), a, b, c, self.on_builtin_change, is_int=True)
            elif field_type == 'text':
                row = TextRow(self.props_container, field_name, cfg.get(field_name, ''), self.on_builtin_change)
            elif field_type == 'color':
                row = ColorRow(self.props_container, field_name, cfg.get(field_name, ''), self.on_builtin_change)
            else:
                row = NumericRow(self.props_container, field_name, cfg.get(field_name, a), a, b, c, self.on_builtin_change)
            row.pack(fill=tk.X, padx=4, pady=2)
            self.property_widgets[field_name] = row

    def on_builtin_select(self, event=None):
        self.gpx_ext_list.selection_clear(0, tk.END)
        self.custom_texts_list.selection_clear(0, tk.END)
        self.build_property_editor_builtin()
        self.refresh()

    def on_gpx_ext_select(self, event=None):
        self.indicator_list.selection_clear(0, tk.END)
        self.custom_texts_list.selection_clear(0, tk.END)
        self.build_property_editor_builtin()
        self.refresh()

    def _rebuild_custom_texts_list(self):
        """Odświeża listbox custom textów."""
        self.custom_texts_list.delete(0, tk.END)
        for idx, ct in enumerate(self.layout.get("custom_texts", [])):
            text_preview = str(ct.get("text", ""))[:30]
            self.custom_texts_list.insert(tk.END, f"{idx}: {text_preview}")

    def on_custom_text_select(self, event=None):
        self.indicator_list.selection_clear(0, tk.END)
        self.gpx_ext_list.selection_clear(0, tk.END)
        self.build_property_editor_builtin()

    def add_custom_text(self):
        """Dodaje nowy custom text do layoutu."""
        self.layout.setdefault("custom_texts", []).append({
            "enabled": True,
            "text": "Nowy tekst",
            "x": 0.5,
            "y": 0.5,
            "rotation": 0,
            "font_size": 0.03,
            "color": "#FFFFFF",
        })
        self._rebuild_custom_texts_list()
        # Zaznacz ostatni
        last_idx = len(self.layout["custom_texts"]) - 1
        self.custom_texts_list.selection_set(last_idx)
        self.build_property_editor_builtin()
        self.refresh()

    def remove_custom_text(self):
        sel = self.custom_texts_list.curselection()
        if not sel:
            return
        idx = sel[0]
        custom_texts = self.layout.get("custom_texts", [])
        if 0 <= idx < len(custom_texts):
            del custom_texts[idx]
            self._rebuild_custom_texts_list()
            self.build_property_editor_builtin()
            self.refresh()

    def on_builtin_change(self):
        # Sprawdź, która lista ma zaznaczenie - priorytet: custom_texts > indicator > gpx_ext
        ct_sel = self.custom_texts_list.curselection()
        if ct_sel:
            idx = ct_sel[0]
            custom_texts = self.layout.get("custom_texts", [])
            if 0 <= idx < len(custom_texts):
                for field_name, widget in self.property_widgets.items():
                    custom_texts[idx][field_name] = widget.get()
                self._rebuild_custom_texts_list()
                # Przywróć zaznaczenie po odświeżeniu listy
                if idx < self.custom_texts_list.size():
                    self.custom_texts_list.selection_set(idx)
                self.refresh()
            return
        sel = self.indicator_list.curselection()
        if sel:
            key = self.indicator_list.get(sel[0])
        else:
            sel = self.gpx_ext_list.curselection()
            if sel:
                key = GPX_EXT_FIELDS[sel[0]]
            else:
                return
        cfg = self.layout['indicators'].get(key)
        if cfg is None:
            return
        for field_name, widget in self.property_widgets.items():
            cfg[field_name] = widget.get()
        self.refresh()

    def update_telemetry_data(self):
        if not self.records:
            return
        try:
            prefer_3d = True
        except:
            prefer_3d = True

        flat = load_telemetry_exiftool(self.video_path)

        # ── GPMF (GoPro telemetry) ──────────────────────────────────────
        self.speed_samples = extract_samples_exiftool(flat)
        speeds = [s for _, s in self.speed_samples]
        smoothed = smooth_speed_values(speeds, window=5)
        self.speed_samples = [
            (self.speed_samples[i][0], smoothed[i])
            for i in range(len(self.speed_samples))
        ]

        if self.speed_samples:
            self.start_dt_utc = self.speed_samples[0][0]
        else:
            self.start_dt_utc = None

        self.track_samples = extract_track_samples(self.records)
        self.alt_samples   = extract_altitude_samples_exiftool(flat)
        self.iso_samples      = extract_iso_samples(self.records)
        self.exposure_samples    = extract_exposure_samples(self.records)
        self.temperature_samples = extract_temperature_samples(self.records)

        if self.alt_samples:
            alts = [a for _, a in self.alt_samples]
            smoothed_alts = smooth_speed_values(alts, window=5)
            self.alt_samples = [
                (self.alt_samples[i][0], smoothed_alts[i])
                for i in range(len(self.alt_samples))
            ]

        # Synchronizacja: szukamy absolutnego startu filmu (T=0)
        anchor = find_gps_anchor(self.records)
        if anchor:
            self.start_dt_utc = anchor
        elif self.speed_samples:
            self.start_dt_utc = self.speed_samples[0][0]

        if self.speed_samples:
            self.speed_samples = smooth_speed_samples(self.speed_samples, "moving_average", SMOOTHING_WINDOW)
        if self.alt_samples:
            self.alt_samples = smooth_speed_samples(self.alt_samples, "moving_average", SMOOTHING_WINDOW)

        # ── GPX ──────────────────────────────────────────────────────────
        # Zapisujemy dane GPX osobno – źródło wybierane per-wskaźnik w layout
        self.gpx_speed_samples = []
        self.gpx_track_samples = []
        self.gpx_alt_samples = []
        self.gpx_power_samples = []
        self.gpx_atemp_samples = []
        self.gpx_hr_samples = []
        self.gpx_cad_samples = []

        if self.video_path:
            manual_gpx = getattr(self, 'gpx_path', None)
            if manual_gpx and Path(manual_gpx).suffix.lower() == '.gpx' and Path(manual_gpx).is_file():
                try:
                    _pts = parse_gpx(manual_gpx)
                    gpx_result = sync_gpx_to_video(_pts, self.start_dt_utc) if _pts else None
                except Exception as _e:
                    print('[GPX] Blad wczytywania recznie wybranego GPX: ' + str(_e), flush=True)
                    gpx_result = None
            else:
                gpx_result = process_gpx(self.video_path, self.start_dt_utc)

            if gpx_result is not None:
                gpx_speed, gpx_track, gpx_alt, gpx_power, gpx_atemp, gpx_hr, gpx_cad = gpx_result
                if gpx_speed:
                    self.gpx_speed_samples = smooth_speed_samples(gpx_speed, "moving_average", SMOOTHING_WINDOW)
                    print("[GPX] gpx_speed_samples: " + str(len(self.gpx_speed_samples)), flush=True)
                if gpx_track:
                    self.gpx_track_samples = gpx_track
                    print("[GPX] gpx_track_samples: " + str(len(self.gpx_track_samples)), flush=True)
                if gpx_alt:
                    self.gpx_alt_samples = smooth_speed_samples(gpx_alt, "moving_average", SMOOTHING_WINDOW)
                    print("[GPX] gpx_alt_samples: " + str(len(self.gpx_alt_samples)), flush=True)
                if gpx_power:
                    self.gpx_power_samples = gpx_power
                    print("[GPX] gpx_power_samples: " + str(len(self.gpx_power_samples)), flush=True)
                if gpx_atemp:
                    self.gpx_atemp_samples = gpx_atemp
                    print("[GPX] gpx_atemp_samples: " + str(len(self.gpx_atemp_samples)), flush=True)
                if gpx_hr:
                    self.gpx_hr_samples = gpx_hr
                    print("[GPX] gpx_hr_samples: " + str(len(self.gpx_hr_samples)), flush=True)
                if gpx_cad:
                    self.gpx_cad_samples = gpx_cad
                    print("[GPX] gpx_cad_samples: " + str(len(self.gpx_cad_samples)), flush=True)
                if self.start_dt_utc is None and gpx_speed:
                    self.start_dt_utc = gpx_speed[0][0]
                # Automatycznie przełącz wskaźniki GPS na źródło GPX (tylko przy auto-wykryciu, nie przy ręcznym wyborze)
                if not getattr(self, 'gpx_path', None):
                    for ind_key in ('speed_visual', 'speed_text', 'dist_visual', 'dist_text', 'alt_visual', 'alt_text'):
                        if ind_key in self.layout.get('indicators', {}):
                            self.layout['indicators'][ind_key]['source'] = 'gpx'
                try:
                    cur = self.meta_info_var.get()
                    if "GPX" not in cur:
                        self.meta_info_var.set(cur.rstrip() + "  |  GPX: OK")
                    if not getattr(self, 'gpx_path', None):
                        auto_path = find_gpx_for_video(self.video_path)
                        if auto_path:
                            self.gpx_info_var.set('GPX: ' + auto_path.name + '  (auto)  ' + str(len(gpx_speed)) + ' pkt')
                except Exception:
                    pass

        # ── FIT ──────────────────────────────────────────────────────────
        # Zapisujemy dane FIT osobno – tylko ręczne wczytywanie, bez auto-wykrywania
        self.fit_speed_samples = []
        self.fit_track_samples = []
        self.fit_alt_samples = []
        self.fit_power_samples = []
        self.fit_atemp_samples = []
        self.fit_hr_samples = []
        self.fit_cad_samples = []

        manual_fit = getattr(self, 'fit_path', None)
        if manual_fit and Path(manual_fit).suffix.lower() == '.fit' and Path(manual_fit).is_file():
            try:
                _pts = parse_fit(manual_fit)
                fit_result = sync_fit_to_video(_pts, self.start_dt_utc) if _pts else None
            except Exception as _e:
                print('[FIT] Blad wczytywania recznie wybranego FIT: ' + str(_e), flush=True)
                fit_result = None

            if fit_result is not None:
                fit_speed, fit_track, fit_alt, fit_power, fit_atemp, fit_hr, fit_cad = fit_result
                if fit_speed:
                    self.fit_speed_samples = smooth_speed_samples(fit_speed, "moving_average", SMOOTHING_WINDOW)
                    print("[FIT] fit_speed_samples: " + str(len(self.fit_speed_samples)), flush=True)
                if fit_track:
                    self.fit_track_samples = fit_track
                    print("[FIT] fit_track_samples: " + str(len(self.fit_track_samples)), flush=True)
                if fit_alt:
                    self.fit_alt_samples = smooth_speed_samples(fit_alt, "moving_average", SMOOTHING_WINDOW)
                    print("[FIT] fit_alt_samples: " + str(len(self.fit_alt_samples)), flush=True)
                if fit_power:
                    self.fit_power_samples = fit_power
                    print("[FIT] fit_power_samples: " + str(len(self.fit_power_samples)), flush=True)
                if fit_atemp:
                    self.fit_atemp_samples = fit_atemp
                    print("[FIT] fit_atemp_samples: " + str(len(self.fit_atemp_samples)), flush=True)
                if fit_hr:
                    self.fit_hr_samples = fit_hr
                    print("[FIT] fit_hr_samples: " + str(len(self.fit_hr_samples)), flush=True)
                if fit_cad:
                    self.fit_cad_samples = fit_cad
                    print("[FIT] fit_cad_samples: " + str(len(self.fit_cad_samples)), flush=True)
                if self.start_dt_utc is None and fit_speed:
                    self.start_dt_utc = fit_speed[0][0]
                # Automatycznie przełącz wskaźniki GPS na źródło FIT (przy ręcznym wczytaniu)
                for ind_key in ('speed_visual', 'speed_text', 'dist_visual', 'dist_text', 'alt_visual', 'alt_text'):
                    if ind_key in self.layout.get('indicators', {}):
                        self.layout['indicators'][ind_key]['source'] = 'fit'
                try:
                    cur = self.meta_info_var.get()
                    if "FIT" not in cur:
                        self.meta_info_var.set(cur.rstrip() + "  |  FIT: OK")
                    self.fit_info_var.set('FIT: ' + Path(manual_fit).name + '  ' + str(len(fit_speed)) + ' pkt')
                except Exception:
                    pass

        print("speed_samples (GPMF):", len(self.speed_samples))
        print("gpx_speed_samples:", len(self.gpx_speed_samples))
        print("fit_speed_samples:", len(self.fit_speed_samples))
        print("alt_samples (GPMF):", len(self.alt_samples))
        print("gpx_alt_samples:", len(self.gpx_alt_samples))
        print("fit_alt_samples:", len(self.fit_alt_samples))


    def open_image(self):
        path = filedialog.askopenfilename(filetypes=[('Obrazy', '*.jpg *.jpeg *.png *.bmp')])
        if not path:
            return
        self.src_img = Image.open(path).convert('RGB')
        layout_path = Path(path).with_suffix('.layout.json')
        self.layout  = normalize_layout(layout_path, *self.src_img.size)
        self.build_property_editor_builtin()
        self.refresh()

    def open_video(self):
        paths = filedialog.askopenfilenames(filetypes=[('Wideo', '*.mp4 *.MP4 *.mov *.MOV')])
        if not paths:
            return
        
        video_paths = sorted([Path(p) for p in paths])
        self.video_paths_to_process = video_paths
        
        # Podstawowa ścieżka dla UI i nazw plików wyjściowych
        self.video_path = video_paths[0]
        
        self.loading_progress.start()
        if len(video_paths) > 1:
            self.video_info_var.set(f"Łączenie {len(video_paths)} plików...")
        else:
            self.video_info_var.set("Wczytywanie wideo...")

        def bg_load():
            try:
                ffprobe_exe = find_executable(str(self.ffprobe_path), [str(self.base_dir / 'ffprobe.exe'), 'ffprobe.exe'])
                ffmpeg_exe  = find_executable('ffmpeg', [str(self.base_dir / 'ffmpeg.exe'), 'ffmpeg.exe'])
                self.ffprobe_exe = ffprobe_exe
                self.ffmpeg_exe = ffmpeg_exe
                if not ffprobe_exe:
                    raise RuntimeError('Nie znaleziono ffprobe')
                
                # Odczytujemy meta z pierwszego pliku dla rozdzielczości/fps, ale sumujemy dur
                first_info = ffprobe_stream_info(ffprobe_exe, video_paths[0])
                streams = first_info.get('streams', [])
                w = int(streams[0].get('width',  1920)) if streams else 1920
                h = int(streams[0].get('height', 1080)) if streams else 1080
                fps = parse_fps(streams[0].get('avg_frame_rate') or streams[0].get('r_frame_rate')) if streams else 30.0
                self.fps = fps
                
                total_dur = 0.0
                for p in video_paths:
                    p_info = ffprobe_stream_info(ffprobe_exe, p)
                    total_dur += float(p_info.get('format', {}).get('duration', 0) or 0)
                self.video_duration_s = total_dur
                
                # Pobranie pierwszej klatki w tle, by uniknąć zacięcia przy pierwszym odświeżeniu
                first_frame = extract_frame(video_paths, 0, ffmpeg_exe, ffprobe_exe) if ffmpeg_exe else None
                
                # Wstępne sprawdzenie i wczytanie telemetrii
                meta_candidate = find_metadata_json(video_paths[0])
                records = []
                if meta_candidate.exists():
                    records = ensure_records_list(load_json_with_fallback(meta_candidate))
                    # Przygotuj dane telemetryczne już w wątku tła
                    self.records = records
                    self.update_telemetry_data()

                def sync_ui():
                    self.src_img = first_frame if first_frame else Image.new('RGB', (w, h), (30, 30, 30))
                    def_layout = self.base_dir / 'def_layout.json'
                    self.layout  = normalize_layout(def_layout, w, h)
                    self.seek_slider.config(to=total_dur, tickinterval=0)
                    self.seek_var.set(0)
                    self.root.after_idle(self.draw_tick_labels)
                    # Ustawiamy na 0, jeśli klatka została pobrana, aby refresh() jej nie pobierał ponownie
                    self.last_preview_timestamp = 0 if first_frame else -1
                    self.video_duration_s = total_dur

                    self.video_info_var.set(f'Video: {w}x{h} @ {fps:.2f}fps, {total_dur:.1f}s ({len(video_paths)} plik(i))')
                    
                    if meta_candidate.exists():
                        self.meta_path = meta_candidate
                        self.meta_info_var.set(f'Meta: {meta_candidate.name}')
                        if not self.speed_samples and not self.track_samples:
                            self.render_stats.config(text='Meta JSON obecne, ale brak próbek. Odczyt...')
                            self.generate_meta_json(video_paths=video_paths, silent=True)
                    else:
                        self.meta_info_var.set('Meta JSON: brak (wygeneruj exiftool)')
                        self.generate_meta_json(video_paths=video_paths, silent=True)

                    # Automatyczny zapis ustawień (layoutu) – tylko gdy def_layout.json nie istnieje
                    try:
                        if not def_layout.exists():
                            with open(def_layout, 'w', encoding='utf-8') as f:
                                json.dump(self.layout, f, indent=2, ensure_ascii=False)
                    except Exception: pass

                    self.build_property_editor_builtin()
                    self.refresh()
                    self.loading_progress.stop()
                
                self.root.after(0, sync_ui)
                
            except Exception as e:
                self.root.after(0, lambda e=e: (
                    self.loading_progress.stop(),
                    messagebox.showerror('Błąd', str(e))
                ))
        threading.Thread(target=bg_load, daemon=True).start()

    def open_telemetry(self):
        """Manually select a .gpx or .fit file and store data separately."""
        path = filedialog.askopenfilename(
            filetypes=[('GPX/FIT', '*.gpx *.GPX *.fit *.FIT'), ('GPX', '*.gpx *.GPX'), ('FIT', '*.fit *.FIT'), ('Wszystkie', '*.*')],
            title='Wybierz plik GPX lub FIT'
        )
        if not path:
            return

        fpath = Path(path)
        suffix = fpath.suffix.lower()

        if suffix == '.fit':
            self.fit_path = fpath
            if not _FIT_AVAILABLE:
                messagebox.showerror('Blad FIT', 'Modul telemetry_fit nie jest dostepny.\nSprawdz czy plik telemetry_fit.py znajduje sie w tym samym katalogu.')
                self.fit_info_var.set('FIT: brak modulu telemetry_fit')
                return
            try:
                points = parse_fit(self.fit_path)
                if not points:
                    messagebox.showwarning('FIT', 'Plik FIT nie zawiera rekordow z czasem.')
                    return
                fit_speed, fit_track, fit_alt, fit_power, fit_atemp, fit_hr, fit_cad = sync_fit_to_video(points, self.start_dt_utc)
                if fit_speed:
                    self.fit_speed_samples = smooth_speed_samples(fit_speed, 'moving_average', SMOOTHING_WINDOW)
                if fit_track:
                    self.fit_track_samples = fit_track
                if fit_alt:
                    self.fit_alt_samples = smooth_speed_samples(fit_alt, 'moving_average', SMOOTHING_WINDOW)
                if fit_power:
                    self.fit_power_samples = fit_power
                if fit_atemp:
                    self.fit_atemp_samples = fit_atemp
                if fit_hr:
                    self.fit_hr_samples = fit_hr
                if fit_cad:
                    self.fit_cad_samples = fit_cad
                if self.start_dt_utc is None and fit_speed:
                    self.start_dt_utc = fit_speed[0][0]

                # Przelacz wskazniki GPS na zrodlo FIT
                for ind_key in ('speed_visual', 'speed_text', 'dist_visual', 'dist_text', 'alt_visual', 'alt_text'):
                    if ind_key in self.layout.get('indicators', {}):
                        self.layout['indicators'][ind_key]['source'] = 'fit'
                self.build_property_editor_builtin()

                n = len(points)
                self.fit_info_var.set(f'FIT: {self.fit_path.name}  ({n} pkt)')
                print(f'[FIT] Wczytano recznie: {self.fit_path}  ({n} punktow)', flush=True)
                self.refresh()
            except Exception as exc:
                messagebox.showerror('Blad FIT', str(exc))
                self.fit_info_var.set('FIT: blad wczytywania')

        else:  # .gpx
            self.gpx_path = fpath
            if not _GPX_AVAILABLE:
                messagebox.showerror('Blad GPX', 'Modul telemetry_gpx nie jest dostepny.\nSprawdz czy plik telemetry_gpx.py znajduje sie w tym samym katalogu.')
                self.gpx_info_var.set('GPX: brak modulu telemetry_gpx')
                return
            try:
                points = parse_gpx(self.gpx_path)
                if not points:
                    messagebox.showwarning('GPX', 'Plik GPX nie zawiera punktow z czasem.')
                    return
                gpx_speed, gpx_track, gpx_alt, gpx_power, gpx_atemp, gpx_hr, gpx_cad = sync_gpx_to_video(points, self.start_dt_utc)
                if gpx_speed:
                    self.gpx_speed_samples = smooth_speed_samples(gpx_speed, 'moving_average', SMOOTHING_WINDOW)
                if gpx_track:
                    self.gpx_track_samples = gpx_track
                if gpx_alt:
                    self.gpx_alt_samples = smooth_speed_samples(gpx_alt, 'moving_average', SMOOTHING_WINDOW)
                if gpx_power:
                    self.gpx_power_samples = gpx_power
                if gpx_atemp:
                    self.gpx_atemp_samples = gpx_atemp
                if gpx_hr:
                    self.gpx_hr_samples = gpx_hr
                if gpx_cad:
                    self.gpx_cad_samples = gpx_cad
                if self.start_dt_utc is None and gpx_speed:
                    self.start_dt_utc = gpx_speed[0][0]

                # Automatycznie przelacz wskazniki GPS na zrodlo GPX
                for ind_key in ('speed_visual', 'speed_text', 'dist_visual', 'dist_text', 'alt_visual', 'alt_text'):
                    if ind_key in self.layout.get('indicators', {}):
                        self.layout['indicators'][ind_key]['source'] = 'gpx'
                self.build_property_editor_builtin()

                n = len(points)
                self.gpx_info_var.set(f'GPX: {self.gpx_path.name}  ({n} pkt)')
                print(f'[GPX] Wczytano recznie: {self.gpx_path}  ({n} punktow)', flush=True)
                self.refresh()
            except Exception as exc:
                messagebox.showerror('Blad GPX', str(exc))
                self.gpx_info_var.set('GPX: blad wczytywania')

    def _get_samples_for_source(self, source_type):
        """Zwraca (speed_samples, track_samples, alt_samples) dla wskazanego źródła."""
        if source_type == 'gpx':
            return (self.gpx_speed_samples or self.speed_samples,
                    self.gpx_track_samples or self.track_samples,
                    self.gpx_alt_samples or self.alt_samples)
        if source_type == 'fit':
            return (self.fit_speed_samples or self.speed_samples,
                    self.fit_track_samples or self.track_samples,
                    self.fit_alt_samples or self.alt_samples)
        return (self.speed_samples, self.track_samples, self.alt_samples)

    def refresh(self):
        self._refresh_after_id = None
        try:
            # Bez wgranego pliku – tylko czarny obraz
            if self.video_path is None:
                pw = self.preview_label.winfo_width()
                ph = self.preview_label.winfo_height()
                if pw > 10 and ph > 10:
                    blank = Image.new('RGB', (pw, ph), (0, 0, 0))
                    self.photo = ImageTk.PhotoImage(blank)
                    self.preview_label.configure(image=self.photo)
                return

            pw = self.preview_label.winfo_width()
            ph = self.preview_label.winfo_height()
            if pw < 10 or ph < 10:
                return

            current_ts = self.seek_var.get()
            if self.video_paths_to_process:
                if abs(current_ts - self.last_preview_timestamp) > 0.25:
                    ffmpeg_exe = self.ffmpeg_exe or find_executable('ffmpeg', [str(self.base_dir / 'ffmpeg.exe'), 'ffmpeg.exe'])
                    ffprobe_exe = self.ffprobe_exe or find_executable('ffprobe', [str(self.base_dir / 'ffprobe.exe'), 'ffprobe.exe'])
                    if ffmpeg_exe and ffprobe_exe:
                        img = extract_frame(self.video_paths_to_process, current_ts, ffmpeg_exe, ffprobe_exe)
                        if img:
                            self.src_img = img
                            self.last_preview_timestamp = current_ts

            # Dane do podglądu (Demo jeśli brak telemetrii)
            speed_val, dist_m, max_dist, alt_val, iso_val, exp_val, temp_val = 45.0, 1500.0, 10000.0, 70.0, 100, 500, 25
            power_val, atemp_val, hr_val, cad_val = 0, 20, 0, 0
            date_txt, time_txt = "2026-06-17", "12:00:00.0"
            indicator_values = {}
            max_speed_kmh = None

            if self.speed_samples and self.start_dt_utc:
                update_rate = self.update_rate_var.get()
                if update_rate == 'Half':
                    N = 2
                elif update_rate == 'Quarter':
                    N = 4
                else:
                    N = 1
                fps = getattr(self, 'fps', 30.0)
                frame_idx = int(round(current_ts * fps))
                calc_frame_idx = max(0, frame_idx - (frame_idx % N))
                calc_ts = calc_frame_idx / fps

                target_dt = self.start_dt_utc + timedelta(seconds=calc_ts)
                if target_dt.tzinfo is None:
                    target_dt = target_dt.replace(tzinfo=timezone.utc)

                # ── Oblicz wartości per-wskaźnik uwzględniając źródło danych ──
                # Dla każdego wskaźnika sprawdzamy źródło w layout i interpolujemy z odpowiednich próbek
                indicator_values = {}
                for ind_key in ('speed_visual', 'speed_text', 'dist_visual', 'dist_text', 'alt_visual', 'alt_text'):
                    ind_cfg = self.layout['indicators'].get(ind_key, {})
                    src = ind_cfg.get('source', 'gpmf')
                    spd_s, trk_s, alt_s = self._get_samples_for_source(src)
                    if ind_key in ('speed_visual', 'speed_text'):
                        indicator_values[ind_key] = interpolate_speed(spd_s, target_dt)
                    elif ind_key in ('dist_visual', 'dist_text'):
                        indicator_values[ind_key] = interpolate_distance(trk_s, target_dt)
                    elif ind_key in ('alt_visual', 'alt_text'):
                        indicator_values[ind_key] = interpolate_altitude(alt_s, target_dt)

                speed_val = indicator_values.get('speed_visual', interpolate_speed(self.speed_samples, target_dt))
                dist_m    = indicator_values.get('dist_visual', interpolate_distance(self.track_samples, target_dt))
                # Debug source switching
                spd_src = self.layout['indicators'].get('speed_visual', {}).get('source', 'gpmf')
                print(f"[REFRESH] speed source={spd_src}  speed_val={speed_val:.4f}  gpx_speed_samples={len(self.gpx_speed_samples)}  speed_samples={len(self.speed_samples)}", flush=True)
                if self.track_samples:
                    max_dist = self.track_samples[-1][1]
                    # Dla dist_visual użyj dystansu z tego samego źródła co wskaźnik
                    dist_src = self.layout['indicators'].get('dist_visual', {}).get('source', 'gpmf')
                    _, trk_s, _ = self._get_samples_for_source(dist_src)
                    if trk_s:
                        max_dist = trk_s[-1][1]

                # max_speed_kmh z odpowiedniego źródła
                max_speed_kmh = None
                spd_src = self.layout['indicators'].get('speed_visual', {}).get('source', 'gpmf')
                spd_s, _, _ = self._get_samples_for_source(spd_src)
                if spd_s:
                    spd_vals = [s for _, s in spd_s]
                    if spd_vals:
                        max_speed_kmh = max(spd_vals)

                if self.alt_samples:
                    alt_val = indicator_values.get('alt_visual', interpolate_altitude(self.alt_samples, target_dt))

                iso_val = interpolate_iso(self.iso_samples if hasattr(self, 'iso_samples') and self.iso_samples else [], target_dt)
                exp_val = interpolate_exposure(self.exposure_samples if hasattr(self, 'exposure_samples') and self.exposure_samples else [], target_dt)
                temp_val = interpolate_temperature(self.temperature_samples if hasattr(self, 'temperature_samples') and self.temperature_samples else [], target_dt)

                # ── Wartości z GPX/FIT extensions ──
                gpx_power = self.gpx_power_samples if hasattr(self, 'gpx_power_samples') and self.gpx_power_samples else []
                fit_power = self.fit_power_samples if hasattr(self, 'fit_power_samples') and self.fit_power_samples else []
                gpx_atemp = self.gpx_atemp_samples if hasattr(self, 'gpx_atemp_samples') and self.gpx_atemp_samples else []
                fit_atemp = self.fit_atemp_samples if hasattr(self, 'fit_atemp_samples') and self.fit_atemp_samples else []
                gpx_hr = self.gpx_hr_samples if hasattr(self, 'gpx_hr_samples') and self.gpx_hr_samples else []
                fit_hr = self.fit_hr_samples if hasattr(self, 'fit_hr_samples') and self.fit_hr_samples else []
                gpx_cad = self.gpx_cad_samples if hasattr(self, 'gpx_cad_samples') and self.gpx_cad_samples else []
                fit_cad = self.fit_cad_samples if hasattr(self, 'fit_cad_samples') and self.fit_cad_samples else []
                power_val = interpolate_value(fit_power or gpx_power, target_dt)
                atemp_val = interpolate_value(fit_atemp or gpx_atemp, target_dt)
                hr_val    = interpolate_value(fit_hr or gpx_hr, target_dt)
                cad_val   = interpolate_value(fit_cad or gpx_cad, target_dt)

                try: tz_off = int(self.tz_offset_var.get())
                except: tz_off = 2

                local_dt = target_dt + timedelta(hours=tz_off)
                date_txt = local_dt.strftime('%Y-%m-%d')
                time_txt = local_dt.strftime('%H:%M:%S')

            try:
                min_alt = None
                max_alt = None
                # Użyj źródła z alt_visual do określenia zakresu
                alt_src = self.layout['indicators'].get('alt_visual', {}).get('source', 'gpmf')
                _, _, alt_s = self._get_samples_for_source(alt_src)
                if alt_s:
                    alts = [a for _, a in alt_s]
                    if alts:
                        min_alt = min(alts)
                        max_alt = max(alts)

                # ── Przygotuj dane wykresów (chart) dla podglądu ──
                total_duration = getattr(self, 'video_duration_s', 1.0)
                try: fps_val = self.fps
                except: fps_val = 30.0
                total_frames = max(1, int(total_duration * fps_val))
                current_position = current_ts / max(1.0, total_duration) if total_duration > 0 else 0.0
                chart_data = {}
                for ind_key, ind_cfg in self.layout.get('indicators', {}).items():
                    if ind_cfg.get('form') == 'chart' and ind_cfg.get('enabled', True):
                        src = ind_cfg.get('source', 'gpmf')
                        if 'speed' in ind_key:
                            spd_s, _, _ = self._get_samples_for_source(src)
                            vals = [v for _, v in spd_s] if spd_s else []
                        elif 'dist' in ind_key:
                            _, trk_s, _ = self._get_samples_for_source(src)
                            vals = [v for _, v in trk_s] if trk_s else []
                        elif 'alt' in ind_key:
                            _, _, alt_s = self._get_samples_for_source(src)
                            vals = [v for _, v in alt_s] if alt_s else []
                        elif 'power' in ind_key:
                            vals = [v for _, v in self.fit_power_samples or self.gpx_power_samples]
                        elif 'hr' in ind_key:
                            vals = [v for _, v in self.fit_hr_samples or self.gpx_hr_samples]
                        elif 'cad' in ind_key:
                            vals = [v for _, v in self.fit_cad_samples or self.gpx_cad_samples]
                        elif 'atemp' in ind_key:
                            vals = [v for _, v in self.fit_atemp_samples or self.gpx_atemp_samples]
                        elif 'iso' in ind_key:
                            vals = [v for _, v in self.iso_samples] if self.iso_samples else []
                        elif 'exposure' in ind_key:
                            vals = [v for _, v in self.exposure_samples] if self.exposure_samples else []
                        elif 'temp' in ind_key and 'atemp' not in ind_key:
                            vals = [v for _, v in self.temperature_samples] if self.temperature_samples else []
                        else:
                            vals = []
                        if vals and len(vals) >= 2:
                            chart_data[ind_key] = vals

                self.indicator_bboxes.clear()
                preview = render_preview(self.src_img, self.layout, self.font_path,
                                         date_txt, time_txt, speed_val, dist_m, max_dist, alt_val, min_alt, max_alt, iso_val, exp_val, temp_val,
                                         indicator_values=indicator_values, max_speed_kmh=max_speed_kmh,
                                         power_value=power_val, atemp_value=atemp_val,
                                         hr_value=hr_val, cad_value=cad_val,
                                         _bboxes=self.indicator_bboxes,
                                         chart_data=chart_data, current_position=current_position)
                preview.thumbnail((pw, ph), Image.LANCZOS)
                self.photo = ImageTk.PhotoImage(preview)
                self.preview_label.configure(image=self.photo)
            except Exception as e:
                self.preview_label.configure(image='', text=f'Błąd podglądu:\n{e}')
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self.preview_label.configure(image='', text=f'Błąd (callback):\n{e}')
            except Exception:
                pass

    def save_configuration(self):
        if not self.video_path:
            messagebox.showerror('Błąd', 'Najpierw wybierz plik MP4.')
            return
        def_layout = self.base_dir / 'def_layout.json'
        with open(def_layout, 'w', encoding='utf-8') as f:
            json.dump(self.layout, f, indent=2, ensure_ascii=False)
        messagebox.showinfo('Zapisano', f'Konfiguracja zapisana:\n{def_layout}')

    def on_font_change(self, event=None):
        self.font_path = resolve_font_path(self.font_style_var.get())
        FONT_CACHE.clear()
        self.refresh()

    def on_outline_change(self):
        self.layout.setdefault("global", {})["text_outline"] = self.outline_var.get()
        self.refresh()

    def load_json(self):
        path = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
        if not path:
            return
        try:
            w, h = self.src_img.size
            self.layout = normalize_layout(path, w, h)
            self.build_property_editor_builtin()
            self.refresh()
        except Exception as e:
            messagebox.showerror('Błąd', str(e))

    def render_now(self, layout_path=None):
        if not self.video_path:
            messagebox.showerror('Błąd', 'Nie wybrano pliku MP4.')
            return
        if layout_path is None:
            layout_path = self.base_dir / 'def_layout.json'
        meta_candidate = self.video_path.with_suffix(".json")
        if not meta_candidate.exists():
            if messagebox.askyesno('Brak JSON', f'Nie znaleziono:\n{meta_candidate}\n\nWygenerować przez exiftool?'):
                self.generate_meta_json(callback=lambda: self.render_now(layout_path=layout_path))
                return
            else:
                return
        encoder       = self.encoder_var.get()
        prefer_3d     = True
        resolution    = self.resolution_var.get()
        video_bitrate = self.video_bitrate_var.get().strip()
        output_file   = sanitize_output_path(self.output_var.get().strip() or 'output_h265.mp4')
        try:
            tz_offset = int(self.tz_offset_var.get())
        except ValueError:
            tz_offset = 2
        if not output_file.is_absolute():
            output_file = self.video_path.parent / output_file

        # Zapisz lokalną konfigurację obok pliku wyjściowego (do użytku tylko dla tego pliku MP4)
        local_config_path = output_file.parent / f"{output_file.stem}.layout.json"
        try:
            local_config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(local_config_path, 'w', encoding='utf-8') as f:
                json.dump(self.layout, f, indent=2, ensure_ascii=False)
            print(f"[CONFIG] Lokalna konfiguracja zapisana: {local_config_path}", flush=True)
        except Exception as exc:
            print(f"[CONFIG] Nie udało się zapisać lokalnej konfiguracji: {exc}", flush=True)

        # Aktualizuj def_layout.json – render_pipeline wczytuje layout z tego pliku
        try:
            with open(self.base_dir / 'def_layout.json', 'w', encoding='utf-8') as f:
                json.dump(self.layout, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            print(f"[CONFIG] Nie udało się zapisać def_layout.json: {exc}", flush=True)

        workers = None
        if self.worker_mode_var.get() == 'manual':
            try:
                workers = max(1, int(self.worker_count_var.get()))
            except Exception:
                pass

        self.render_stats.config(text="Rozpoczynanie renderowania...")
        self.render_progress.config(mode='determinate', value=0)
        self.render_cancel_event.clear()
        self.render_button.config(text='Anuluj', command=self.cancel_render)

        def run_render():
            try:
                t_export_start = time.time()
                print(f"Render start: input={self.video_paths_to_process}, output={output_file}")
                self.root.after(0, lambda: self.render_stats.config(text="Render w tle..."))
                stats = self.render_pipeline(
                    input_file=self.video_paths_to_process,
                    meta_path=meta_candidate,
                    layout_path=layout_path,
                    output_file=output_file,
                    encoder=encoder,
                    prefer_3d=prefer_3d,
                    resolution=resolution,
                    video_bitrate=video_bitrate,
                    workers=workers,
                    tz_offset=tz_offset
                )
                t_export_end = time.time()
                export_duration = t_export_end - t_export_start
                if not self.render_cancel_event.is_set() and stats:
                    self.root.after(0, lambda: self.show_statistics_dialog(stats, export_duration, output_file))
            except Exception as e:
                err_msg = str(e)
                print('Render thread exception:', err_msg)
                self.root.after(0, lambda msg=err_msg: messagebox.showerror('Błąd renderowania', msg))
            finally:
                self.root.after(0, self._on_render_finished)

        threading.Thread(target=run_render, daemon=True).start()

    def cancel_render(self):
        self.render_cancel_event.set()
        if isinstance(self._active_process, dict):
            process = self._active_process.get('process')
            if process is not None:
                try:
                    process.terminate()
                except Exception:
                    pass
        elif self._active_process is not None:
            try:
                self._active_process.terminate()
            except Exception:
                pass
        self.render_stats.config(text='Przerywanie renderowania...')
        self.render_button.config(state='disabled')

    def _on_render_finished(self):
        self.render_button.config(text='Render teraz', command=self.render_now, state='normal')
        if self.render_cancel_event.is_set():
            self.render_stats.config(text='Anulowano')
        else:
            self.render_stats.config(text='Gotowy')
        self.render_progress.stop()
        self.render_progress.config(value=0, mode='determinate')

    def show_statistics_dialog(self, stats, export_duration, output_file):
        dialog = tk.Toplevel(self.root)
        dialog.title("Statystyki eksportu")
        dialog.geometry("500x320")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        try:
            x = self.root.winfo_x() + (self.root.winfo_width() - 500) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - 320) // 2
            dialog.geometry(f"+{x}+{y}")
        except Exception:
            pass

        title_label = ttk.Label(dialog, text="Eksport zakończony pomyślnie!", font=("Segoe UI", 12, "bold"))
        title_label.pack(pady=(15, 5))

        desc_label = ttk.Label(dialog, text=f"Plik: {Path(output_file).name}", font=("Segoe UI", 9, "italic"))
        desc_label.pack(pady=(0, 15))

        table_frame = ttk.Frame(dialog, padding=10)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

        ttk.Label(table_frame, text="Etap", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        ttk.Label(table_frame, text="Czas trwania", font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w", padx=10, pady=5)
        ttk.Label(table_frame, text="Średnia wydajność", font=("Segoe UI", 10, "bold")).grid(row=0, column=2, sticky="w", padx=10, pady=5)

        ttk.Separator(table_frame, orient='horizontal').grid(row=1, column=0, columnspan=3, sticky='ew', pady=5)

        def fmt_time(seconds):
            if seconds >= 60:
                mins = int(seconds // 60)
                secs = seconds % 60
                return f"{mins} min {secs:.1f} s"
            return f"{seconds:.2f} s"

        def fmt_fps(frames, duration):
            if duration <= 0:
                return "0.0 fps"
            return f"{frames / duration:.1f} fps"

        # 1. Total Export
        total_time_str = fmt_time(export_duration)
        total_fps_str = fmt_fps(stats['final_frames'], export_duration)
        
        ttk.Label(table_frame, text="Od naciśnięcia Export", font=("Segoe UI", 10, "bold")).grid(row=2, column=0, sticky="w", padx=10, pady=5)
        ttk.Label(table_frame, text=total_time_str, font=("Segoe UI", 10, "bold")).grid(row=2, column=1, sticky="w", padx=10, pady=5)
        ttk.Label(table_frame, text=total_fps_str, font=("Segoe UI", 10, "bold")).grid(row=2, column=2, sticky="w", padx=10, pady=5)

        # 2. Streaming render (jednoetapowy)
        png_time_str = fmt_time(stats['png_duration'])
        png_fps_str = fmt_fps(stats['total_overlay_frames'], stats['png_duration'])
        
        ttk.Label(table_frame, text="Render HUD + kompresja").grid(row=3, column=0, sticky="w", padx=10, pady=5)
        ttk.Label(table_frame, text=png_time_str).grid(row=3, column=1, sticky="w", padx=10, pady=5)
        ttk.Label(table_frame, text=png_fps_str).grid(row=3, column=2, sticky="w", padx=10, pady=5)

        btn = ttk.Button(dialog, text="OK", command=dialog.destroy)
        btn.pack(pady=15)

    def generate_meta_json(self, video_paths=None, silent=False, callback=None):
        paths = video_paths or self.video_paths_to_process or ([self.video_path] if self.video_path else [])
        if not paths:
            return

        self.render_stats.config(text="Generowanie telemetrii (ExifTool)...")
        self.render_progress.config(mode='indeterminate')
        self.render_progress.start()

        def worker():
            try:
                print("➡ USING EXIFTOOL ONLY")

                exiftool_exe = find_executable(
                    str(self.exiftool_path),
                    [str(self.base_dir / 'exiftool.exe'), 'exiftool.exe']
                )

                if not exiftool_exe:
                    raise RuntimeError("❌ Nie znaleziono exiftool")

                self.exiftool_path = exiftool_exe

                # ✅ WYWOŁANIE EXIFTOOL
                cmd = [
                    exiftool_exe,
                    "-ee",
                    "-j",
                    "-G3",
                    str(paths[0])
                ]

                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr or "ExifTool error")

                data = json.loads(proc.stdout)
                if not data:
                    raise RuntimeError("❌ ExifTool zwrócił puste dane")

                flat = data[0]

                # ✅ ZAPIS DO TEGO SAMEGO KATALOGU CO VIDEO
                json_path = self.video_path.with_suffix(".json")

                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(flat, f, indent=2, ensure_ascii=False)

                print(f"✅ JSON zapisany: {json_path}")

                # ✅ WAŻNE – dalsza część programu
                self.records = [flat]
                self.update_telemetry_data()

                def success():
                    self.render_progress.stop()
                    self.render_progress.config(mode='determinate', value=0)
                    self.render_stats.config(text="Gotowy")

                    self.meta_path = json_path
                    self.meta_info_var.set(f'Meta: {json_path.name}')

                    self.refresh()

                    if not silent:
                        messagebox.showinfo('OK', f'JSON wygenerowany:\n{json_path}')

                    if callback:
                        callback()

                self.root.after(0, success)

            except Exception as e:
                err_text = str(e)

                def error(err=err_text):
                    self.render_progress.stop()
                    self.render_progress.config(mode='determinate', value=0)
                    self.render_stats.config(text="Błąd")
                    messagebox.showerror('Błąd telemetrii', err)

                self.root.after(0, error)

        threading.Thread(target=worker, daemon=True).start()

    def render_pipeline(self, input_file, meta_path, layout_path, output_file,
                        encoder, prefer_3d, resolution, video_bitrate, 
                        workers=None, tz_offset=2):
        ffmpeg_exe  = self.ffmpeg_exe or find_executable('ffmpeg',  [str(self.base_dir / 'ffmpeg.exe'), 'ffmpeg.exe'])
        ffprobe_exe = self.ffprobe_exe or find_executable(str(self.ffprobe_path), [str(self.base_dir / 'ffprobe.exe'), 'ffprobe.exe'])
        self.ffmpeg_exe = ffmpeg_exe
        self.ffprobe_exe = ffprobe_exe
        if not ffmpeg_exe:
            raise RuntimeError('Nie znaleziono ffmpeg.exe.')
        if not ffprobe_exe:
            raise RuntimeError('Nie znaleziono ffprobe.exe.')
        if not Path(self.font_path).exists() and not any(p in str(self.font_path) for p in ('/', '\\')):
            # Font is a family name, not a path – try loading it, fallback to default
            pass
        elif not Path(self.font_path).exists():
            raise RuntimeError(f'Nie znaleziono czcionki: {self.font_path}')

        # Obsługa listy plików wejściowych
        primary_input = input_file[0] if isinstance(input_file, list) else input_file
        info    = ffprobe_stream_info(ffprobe_exe, primary_input)
        streams = info.get('streams', [])
        fps          = parse_fps(streams[0].get('avg_frame_rate') or streams[0].get('r_frame_rate')) if streams else 30.0
        video_width  = int(streams[0].get('width',  1920)) if streams else 1920
        video_height = int(streams[0].get('height', 1080)) if streams else 1080
        
        duration_s = self.video_duration_s if isinstance(input_file, list) and input_file == self.video_paths_to_process and self.video_duration_s > 0 else 0.0
        if duration_s <= 0.0:
            if isinstance(input_file, list):
                for p in input_file:
                    duration_s += float(ffprobe_stream_info(ffprobe_exe, p).get('format', {}).get('duration', 0))
            else:
                duration_s = float(info.get('format', {}).get('duration', 0))

        if duration_s <= 0:
            raise RuntimeError('Nie udało się odczytać długości filmu.')

        target_res = RESOLUTION_MAP.get(resolution)
        render_width, render_height = (video_width, video_height) if target_res is None else target_res

        records = ensure_records_list(load_json_with_fallback(meta_path))

        # --- v4.1.3: odczytaj rotację PRZED generowaniem klatek ---
        rotation_degrees = get_rotation_from_metadata(records)
        container_rotation = get_container_rotation(ffprobe_exe, input_file)

        # Manual override from UI: 'auto' or one of 0/90/180/270
        rotation_override = self.rotation_var.get() if hasattr(self, 'rotation_var') else 'auto'
        if rotation_override != 'auto':
            effective_rotation = int(rotation_override)
            # when user forces rotation, disable container auto-rotate handling
            container_rotation_arg = 0
        else:
            # container rotation tag is the primary indicator of actual pixel orientation.
            # When container has no rotation tag (0), fall back to GoPro metadata:
            # some files have pixels NOT pre-rotated by the camera but DO have AutoRotation
            # metadata indicating the correct orientation.
            effective_rotation = container_rotation if container_rotation != 0 else rotation_degrees
            container_rotation_arg = container_rotation

        print(f"[ROTATION] container={container_rotation}  metadata={rotation_degrees}  "
              f"override={rotation_override}  effective={effective_rotation}", flush=True)

        overlay_width, overlay_height = render_width, render_height
        if effective_rotation in (90, 270):
            overlay_width, overlay_height = render_height, render_width

        layout   = normalize_layout(layout_path, overlay_width, overlay_height)

        gpmf_speed = extract_speed_samples(records, prefer_3d=prefer_3d)
        gpmf_speed = smooth_speed_samples(gpmf_speed, "moving_average", SMOOTHING_WINDOW)
        gpmf_track = extract_track_samples(records)
        gpmf_alt   = extract_altitude_samples(records)
        iso_samples          = extract_iso_samples(records)
        exposure_samples     = extract_exposure_samples(records)
        temperature_samples  = extract_temperature_samples(records)
        if gpmf_alt:
            gpmf_alt = smooth_speed_samples(gpmf_alt, "moving_average", SMOOTHING_WINDOW)

        # Użyj start_dt_utc z GUI – FIT/GPX były do niego zsynchronizowane
        start_dt_utc = getattr(self, 'start_dt_utc', None)
        if start_dt_utc is None:
            anchor = find_gps_anchor(records)
            if anchor:
                start_dt_utc = anchor
            elif gpmf_speed:
                start_dt_utc = gpmf_speed[0][0]
            else:
                start_dt_utc = None

        # GPX: osobne próbki – źródło wybierane per-wskaźnik
        gpx_speed_samples = []
        gpx_track_samples = []
        gpx_alt_samples = []
        gpx_power_samples = []
        gpx_atemp_samples = []
        gpx_hr_samples = []
        gpx_cad_samples = []
        gpx_result = process_gpx(primary_input, start_dt_utc)
        if gpx_result is not None:
            gpx_speed, gpx_track, gpx_alt, gpx_power, gpx_atemp, gpx_hr, gpx_cad = gpx_result
            if gpx_speed:
                gpx_speed_samples = smooth_speed_samples(gpx_speed, "moving_average", SMOOTHING_WINDOW)
                print("[GPX] render: gpx_speed_samples: " + str(len(gpx_speed_samples)), flush=True)
            if gpx_track:
                gpx_track_samples = gpx_track
                print("[GPX] render: gpx_track_samples: " + str(len(gpx_track_samples)), flush=True)
            if gpx_alt:
                gpx_alt_samples = smooth_speed_samples(gpx_alt, "moving_average", SMOOTHING_WINDOW)
                print("[GPX] render: gpx_alt_samples: " + str(len(gpx_alt_samples)), flush=True)
            if start_dt_utc is None and gpx_speed:
                start_dt_utc = gpx_speed[0][0]

        # FIT: osobne próbki – używamy już załadowanych z GUI (self.fit_*_samples)
        fit_speed_samples = list(getattr(self, 'fit_speed_samples', []) or [])
        fit_track_samples = list(getattr(self, 'fit_track_samples', []) or [])
        fit_alt_samples   = list(getattr(self, 'fit_alt_samples', []) or [])
        fit_power_samples = list(getattr(self, 'fit_power_samples', []) or [])
        fit_atemp_samples = list(getattr(self, 'fit_atemp_samples', []) or [])
        fit_hr_samples    = list(getattr(self, 'fit_hr_samples', []) or [])
        fit_cad_samples   = list(getattr(self, 'fit_cad_samples', []) or [])

        # Fallback: parsuj z pliku tylko jeśli GUI nie ma danych
        manual_fit = getattr(self, 'fit_path', None)
        if not any([fit_speed_samples, fit_hr_samples, fit_cad_samples]):
            if manual_fit and Path(manual_fit).suffix.lower() == '.fit' and Path(manual_fit).is_file():
                try:
                    _pts = parse_fit(manual_fit)
                    fit_result = sync_fit_to_video(_pts, start_dt_utc) if _pts else None
                except Exception as _e:
                    print('[FIT] render: blad wczytywania FIT: ' + str(_e), flush=True)
                    fit_result = None
                if fit_result is not None:
                    fit_speed, fit_track, fit_alt, fit_power, fit_atemp, fit_hr, fit_cad = fit_result
                    if fit_speed:
                        fit_speed_samples = smooth_speed_samples(fit_speed, "moving_average", SMOOTHING_WINDOW)
                        print("[FIT] render: fit_speed_samples: " + str(len(fit_speed_samples)), flush=True)
                    if fit_track:
                        fit_track_samples = fit_track
                        print("[FIT] render: fit_track_samples: " + str(len(fit_track_samples)), flush=True)
                    if fit_alt:
                        fit_alt_samples = smooth_speed_samples(fit_alt, "moving_average", SMOOTHING_WINDOW)
                        print("[FIT] render: fit_alt_samples: " + str(len(fit_alt_samples)), flush=True)
                    if start_dt_utc is None and fit_speed:
                        start_dt_utc = fit_speed[0][0]

        # Fallback: jeśli GPX puste, używamy GPMF jako źródła
        speed_samples = gpmf_speed
        track_samples = gpmf_track
        alt_samples   = gpmf_alt

        if not speed_samples:
            raise RuntimeError(f'Nie znaleziono próbek prędkości w pliku: {meta_path}')
        if not track_samples:
            raise RuntimeError(f'Nie znaleziono próbek GPS do dystansu w pliku: {meta_path}')

        field_samples = {
            'speed_samples': gpmf_speed,
            'track_samples': gpmf_track,
            'alt_samples': gpmf_alt,
        }
        # max_distance_m z domyślnego źródła (gpmf), render_overlay_job dobierze per-wskaźnik
        max_distance_m = gpmf_track[-1][1] if gpmf_track else 0

        update_rate_str = self.update_rate_var.get() if hasattr(self, 'update_rate_var') else 'Full'
        if update_rate_str == 'Half':
            update_rate_step = 2
        elif update_rate_str == 'Quarter':
            update_rate_step = 4
        else:
            update_rate_step = 1

        generation_fps = fps / update_rate_step
        total_overlay_frames = max(1, math.ceil(duration_s * generation_fps))

        def update_ui(val, stats):
            self.root.after(0, lambda: (
                self.render_progress.config(mode='determinate'),
                self.render_progress.config(value=val),
                self.render_stats.config(text=stats)
            ))

        # ── NOWY PIPELINE: Producent-Konsument → pipe do FFmpeg ──
        self.render_progress['maximum'] = total_overlay_frames
        update_ui(0, "Renderowanie HUD (stream)...")
        self._active_process = {'process': None}
        t_render_start = time.time()

        stream_overlay_to_ffmpeg(
            ffmpeg_exe=ffmpeg_exe,
            input_files=input_file,
            output_file=sanitize_output_path(output_file),
            duration_s=duration_s,
            start_dt_utc=start_dt_utc,
            tz_offset_hours=tz_offset,
            speed_samples=speed_samples,
            track_samples=track_samples,
            alt_samples=alt_samples,
            font_path=self.font_path,
            layout=layout,
            field_samples=field_samples,
            target_fps=fps,
            update_rate_step=update_rate_step,
            max_distance_m=max_distance_m,
            workers=workers,
            iso_samples=iso_samples,
            exposure_samples=exposure_samples,
            temperature_samples=temperature_samples,
            gpx_speed_samples=gpx_speed_samples,
            gpx_track_samples=gpx_track_samples,
            gpx_alt_samples=gpx_alt_samples,
            gpx_power_samples=gpx_power_samples,
            gpx_atemp_samples=gpx_atemp_samples,
            gpx_hr_samples=gpx_hr_samples,
            gpx_cad_samples=gpx_cad_samples,
            fit_speed_samples=fit_speed_samples,
            fit_track_samples=fit_track_samples,
            fit_alt_samples=fit_alt_samples,
            fit_power_samples=fit_power_samples,
            fit_atemp_samples=fit_atemp_samples,
            fit_hr_samples=fit_hr_samples,
            fit_cad_samples=fit_cad_samples,
            progress_cb=update_ui,
            cancel_event=self.render_cancel_event,
            active_process_holder=self._active_process,
            encoder=encoder,
            gpu=0,
            resolution_name=resolution,
            video_bitrate=video_bitrate,
            rotation_degrees=effective_rotation,
            container_rotation=container_rotation_arg,
            overlay_w=overlay_width,
            overlay_h=overlay_height,
            render_w=render_width,
            render_h=render_height,
        )
        t_render_end = time.time()
        render_duration = t_render_end - t_render_start

        if self.render_cancel_event.is_set():
            return

        return {
            'total_overlay_frames': total_overlay_frames,
            'final_frames': int(duration_s * fps),
            'png_duration': render_duration,
            'mov_duration': 0,
            'final_duration': 0,
        }


if __name__ == '__main__':
    root = tk.Tk()
    app  = HudTunerApp(root)
    root.mainloop()