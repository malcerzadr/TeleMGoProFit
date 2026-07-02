# TeleMGP

**TeleMGP** to zaawansowane narzędzie desktopowe z graficznym interfejsem użytkownika (GUI) napisanym w języku Python (Tkinter). Aplikacja służy do odczytywania, synchronizowania oraz nakładania danych telemetrycznych (takich jak prędkość, tętno, kadencja, moc, wysokość czy trasa GPS) bezpośrednio na nagrania wideo z kamer sportowych (np. GoPro) lub innych źródeł.

Aplikacja umożliwia tworzenie profesjonalnych nakładek (overlayów) z wykresami, wskaźnikami oraz mapami tras, co jest idealnym rozwiązaniem dla kolarzy, biegaczy oraz miłośników sportów motorowych i ekstremalnych.

---

## 🚀 Główne Funkcje

- **Wielozadaniowe źródła telemetrii**:
  - **GoPro GPMF**: Bezpośrednie wyciąganie wbudowanych metadanych telemetrycznych z plików wideo kamer GoPro.
  - **Pliki Garmin FIT**: Pełna obsługa plików `.fit` (tętno, kadencja, moc, temperatura, wysokość, prędkość).
  - **Pliki GPX**: Import tras, punktów wysokościowych i parametrów z dowolnego urządzenia rejestrującego w tym formacie.
- **Bogaty zestaw wskaźników (Widgets)**:
  - Tekstowe wyświetlanie bieżących wartości.
  - Tradycyjne wskaźniki wskazówkowe (Gauges).
  - Wykresy słupkowe (Bars).
  - Wykresy historyczne z przebiegiem parametrów (np. tętno w czasie).
  - Automatyczne rysowanie trasy GPS w formie mini-mapy z pozycją kursora.
- **Konfigurowalny układ (Layouts)**:
  - Dowolna personalizacja pozycji, rozmiarów, kolorów, przezroczystości oraz czcionek każdego elementu za pomocą pliku konfiguracyjnego `def_layout.json`.
- **Wydajne renderowanie**:
  - Wsparcie dla kodowania sprzętowego (NVIDIA NVENC, Intel QuickSync oraz klasyczne renderowanie procesorem CPU).
  - Wielordzeniowe przetwarzanie danych (Multiprocessing) w celu szybszego generowania klatek wideo.
  - Elastyczność wyboru rozdzielczości wyjściowej (od 480p, przez 1080p, aż po 4K, 5.3K oraz 8K).

---

## 🛠️ Wymagania i Instalacja

### 1. Środowisko Python
Aplikacja wymaga zainstalowanego środowiska **Python 3.8+**.

### 2. Instalacja zależności bibliotecznych
Zainstaluj wymagane pakiety Pythona przy użyciu pliku `requirements.txt`:

```bash
pip install -r requirements.txt
```

Zależności obejmują:
- **Pillow** (`pillow`) – generowanie i rysowanie wskaźników graficznych.
- **fitparse** – parsowanie plików treningowych Garmin `.fit`.
- **orjson** – (opcjonalnie) szybki parser plików JSON przyspieszający wczytywanie konfiguracji.

### 3. Zewnętrzne narzędzia (ExifTool)
Aplikacja wykorzystuje narzędzie **ExifTool** do odczytu metadanych z plików wideo. W systemie Windows plik `exiftool.exe` oraz katalog `exiftool_files` powinny znajdować się bezpośrednio w głównym katalogu aplikacji (lub być dodane do zmiennej środowiskowej PATH).

---

## 💻 Uruchomienie i Obsługa

Uruchom główny skrypt aplikacji za pomocą komendy:

```bash
python TeleMGP0.16.8.py
```

### Krok po kroku:
1. **Wczytaj Wideo**: Kliknij przycisk wyboru pliku wideo z kamery GoPro.
2. **Załaduj Dane**: Aplikacja spróbuje automatycznie odnaleźć powiązany plik `.fit` lub `.gpx` w tym samym katalogu. Możesz również wybrać go ręcznie.
3. **Konfiguracja Nakładki**: Wybierz parametry, które chcesz wyświetlić (np. prędkość, tętno, moc) oraz ich formę wizualną.
4. **Wybierz ustawienia enkodera**: Dostosuj rozdzielczość docelową oraz wybierz enkoder wideo (NVidia, Intel lub CPU).
5. **Eksport**: Uruchom renderowanie. Wynikowe wideo zostanie zapisane we wskazanym folderze z nałożoną telemetrią.

---

## 🎨 Konfiguracja Układów (Layout)

Wygląd wskaźników kontrolowany jest przez szablon zapisany w formacie JSON (domyślnie `def_layout.json`). Możesz definiować w nim:
- Pozycje elementów na ekranie (współrzędne X, Y).
- Paletę kolorów (w formacie HEX).
- Rozmiary czcionek i grubość linii wykresów.
- Zakresy minimalne i maksymalne wskaźników (np. limit prędkości czy tętna).

---

## 📁 Struktura Projektu

- `TeleMGP0.16.8.py` – Główny skrypt aplikacji zawierający logikę GUI i renderowania.
- `telemetry_fit.py` – Moduł odpowiedzialny za parsowanie i synchronizację plików FIT.
- `telemetry_gpx.py` – Moduł odpowiedzialny za parsowanie i synchronizację plików GPX.
- `def_layout.json` – Plik konfiguracyjny domyślnego układu wskaźników.
- `exiftool.exe` / `exiftool_files/` – Narzędzie i biblioteki ExifTool do ekstrakcji metadanych.
