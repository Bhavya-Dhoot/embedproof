"""Download the public source files and store compact, hash-recorded raw snapshots in data/raw/.

Run from the repo root:  PYTHONPATH=. .venv/Scripts/python.exe data/fetch_raw.py

Sources (see data/SOURCES.md for licences and how each file is used):
  * Climate TRACE v5.10.0 manufacturing sector package (monthly, per-source iron-and-steel CSVs)
  * Climate TRACE API v6 (country list + a small iron-and-steel asset sample for Turkiye)
  * Ember yearly electricity data, long format (only the 'CO2 intensity' rows are kept)
  * European Commission CBAM default values workbook, definitive period (Annex I of Reg. (EU) 2025/2621
    as corrected by Reg. (EU) 2026/1740)

Only the files the model actually uses are kept; the sha256 of every full upstream file is recorded in
data/raw/MANIFEST.json so the filtered snapshots can be audited against the originals.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"

CT_ZIP_URL = "https://downloads.climatetrace.org/latest/sector_packages/co2e_100yr/manufacturing.zip"
CT_MEMBERS = {
    "DATA/iron-and-steel_emissions_sources_v5_10_0.csv": ("climate_trace", True),
    "DATA/iron-and-steel_emissions_sources_confidence_v5_10_0.csv": ("climate_trace", True),
    "ABOUT_THE_DATA/detailed_data_schema_v5_10_0.csv": ("climate_trace", False),
}
CT_API = "https://api.climatetrace.org/v6"
EMBER_URL = "https://storage.googleapis.com/emb-prod-bkt-publicdata/public-downloads/yearly_full_release_long_format.csv"
CBAM_URL = (
    "https://taxation-customs.ec.europa.eu/document/download/1c05d211-80cb-4aaa-8ef0-e08005a95d7e_en"
    "?filename=DVs%20as%20adopted_v20260204%20.xlsx"
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def download(client: httpx.Client, url: str, dest: Path) -> None:
    with client.stream("GET", url, follow_redirects=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)


def write_gz(src: Path, dest: Path) -> None:
    """Deterministic gzip (mtime=0, no embedded filename) so the snapshot hash is stable."""
    with open(src, "rb") as fi, open(dest, "wb") as fo:
        with gzip.GzipFile(filename="", mode="wb", fileobj=fo, compresslevel=9, mtime=0) as gz:
            shutil.copyfileobj(fi, gz, 1 << 20)


def main() -> None:
    manifest: dict = {"retrievedAtUtc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": []}
    (RAW / "climate_trace").mkdir(parents=True, exist_ok=True)
    (RAW / "ember").mkdir(parents=True, exist_ok=True)
    (RAW / "cbam").mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="embedproof_raw_") as tmp_s, httpx.Client() as client:
        tmp = Path(tmp_s)

        # --- Climate TRACE bulk sector package -------------------------------------------------
        zpath = tmp / "manufacturing.zip"
        download(client, CT_ZIP_URL, zpath)
        manifest["files"].append({"upstream": CT_ZIP_URL, "kept": False, "sha256": sha256_file(zpath),
                                  "bytes": zpath.stat().st_size, "note": "full upstream zip, not stored"})
        with zipfile.ZipFile(zpath) as zf:
            for member, (folder, gz) in CT_MEMBERS.items():
                data = zf.read(member)
                name = Path(member).name
                full_sha = sha256_bytes(data)
                if gz:
                    out = RAW / folder / (name + ".gz")
                    tmp_plain = tmp / name
                    tmp_plain.write_bytes(data)
                    write_gz(tmp_plain, out)
                else:
                    out = RAW / folder / name
                    out.write_bytes(data)
                manifest["files"].append({
                    "upstream": f"{CT_ZIP_URL}#{member}", "kept": out.relative_to(ROOT).as_posix(),
                    "sha256_uncompressed": full_sha, "sha256_stored": sha256_file(out), "bytes_stored": out.stat().st_size})

        # --- Climate TRACE API v6 --------------------------------------------------------------
        countries = client.get(f"{CT_API}/definitions/countries", timeout=60).json()
        out = RAW / "climate_trace" / "api_v6_definitions_countries.json"
        out.write_text(json.dumps(countries, ensure_ascii=False, indent=1), encoding="utf-8")
        manifest["files"].append({"upstream": f"{CT_API}/definitions/countries", "kept": out.relative_to(ROOT).as_posix(),
                                  "sha256_stored": sha256_file(out)})

        r = client.get(f"{CT_API}/assets", params={"subsectors": "iron-and-steel", "countries": "TUR",
                                                    "limit": 100, "year": 2024}, timeout=120)
        r.raise_for_status()
        sample = r.json()
        for a in sample.get("assets", []):
            a.pop("Thumbnail", None)  # URL embeds a third-party map-tile access token; not needed
        out = RAW / "climate_trace" / "api_v6_assets_TUR_iron-and-steel_year2024.json"
        out.write_text(json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8")
        manifest["files"].append({"upstream": f"{CT_API}/assets?subsectors=iron-and-steel&countries=TUR&limit=100&year=2024",
                                  "kept": out.relative_to(ROOT).as_posix(), "sha256_stored": sha256_file(out),
                                  "note": "Thumbnail field removed (contains a map-tile token)"})

        # --- Ember yearly CO2 intensity --------------------------------------------------------
        epath = tmp / "ember_yearly.csv"
        download(client, EMBER_URL, epath)
        full_sha = sha256_file(epath)
        out = RAW / "ember" / "ember_yearly_co2_intensity_gco2_per_kwh.csv"
        with open(epath, newline="", encoding="utf-8") as fi, open(out, "w", newline="", encoding="utf-8") as fo:
            rd = csv.DictReader(fi)
            wr = csv.DictWriter(fo, fieldnames=rd.fieldnames, lineterminator="\n")
            wr.writeheader()
            n = 0
            for row in rd:
                if row["Variable"] == "CO2 intensity" and row["Unit"] == "gCO2/kWh":
                    wr.writerow(row)
                    n += 1
        manifest["files"].append({"upstream": EMBER_URL, "kept": out.relative_to(ROOT).as_posix(), "rows": n,
                                  "sha256_uncompressed_full_upstream": full_sha, "sha256_stored": sha256_file(out),
                                  "note": "filtered to Variable == 'CO2 intensity' (Power sector emissions)"})

        # --- EC CBAM default values ------------------------------------------------------------
        out = RAW / "cbam" / "cbam_default_values_definitive_period_v2_2026-08-06.xlsx"
        download(client, CBAM_URL, out)
        manifest["files"].append({"upstream": CBAM_URL, "kept": out.relative_to(ROOT).as_posix(), "sha256_stored": sha256_file(out),
                                  "bytes_stored": out.stat().st_size})

    (RAW / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
