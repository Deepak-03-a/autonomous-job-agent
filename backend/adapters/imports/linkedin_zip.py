"""Read the few files we need from a LinkedIn "Get a copy of your data" archive.

The archive also holds messages, connections, ad-targeting data and more. This
reader opens only an explicit allowlist of members, never extracts anything to
disk, and refuses oversized members, so the rest is never read at all.
"""

import csv
import io
import re
import zipfile

MAX_MEMBER_BYTES = 5 * 1024 * 1024     # per CSV; real files are far smaller
MAX_MEMBERS_SCANNED = 5000

# canonical table name -> normalised file names (letters and digits only, no extension)
ALLOWED = {
    "profile": {"profile"},
    "positions": {"positions"},
    "skills": {"skills"},
    "education": {"education"},
    "certifications": {"certifications"},
    "saved_jobs": {"savedjobs"},
    "applications": {"jobapplications", "applications"},
    "alerts": {"savedjobalerts", "jobalerts", "savedsearches"},
    "preferences": {"jobseekerpreferences"},
}
_BY_FILE = {name: table for table, names in ALLOWED.items() for name in names}


class ExportError(ValueError):
    pass


def _norm(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    base = re.sub(r"\.csv$", "", base, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", base.casefold())


def _rows(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    # Some LinkedIn files start with explanatory notes; the header is the first
    # line with several comma-separated cells.
    start = next((i for i, line in enumerate(lines[:15]) if line.count(",") >= 1 and not line.lstrip().lower().startswith("notes")), 0)
    return [dict(row) for row in csv.DictReader(io.StringIO("\n".join(lines[start:])))]


def read_export(data: bytes) -> tuple[dict[str, list[dict]], list[str], list[str]]:
    """Return (tables, warnings, files_read). Raises ExportError for non-archives."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ExportError("That file is not a zip archive.") from error

    tables: dict[str, list[dict]] = {}
    warnings: list[str] = []
    files_read: list[str] = []
    members = archive.infolist()
    if len(members) > MAX_MEMBERS_SCANNED:
        raise ExportError("The archive has too many files.")

    for info in members:
        if info.is_dir() or not info.filename.lower().endswith(".csv"):
            continue
        table = _BY_FILE.get(_norm(info.filename))
        if table is None or table in tables:
            continue
        if info.file_size > MAX_MEMBER_BYTES:
            warnings.append(f"{info.filename} is larger than {MAX_MEMBER_BYTES // (1024 * 1024)} MB and was skipped.")
            continue
        with archive.open(info) as handle:
            raw = handle.read(MAX_MEMBER_BYTES + 1)
        if len(raw) > MAX_MEMBER_BYTES:   # the declared size can lie; the read cannot
            warnings.append(f"{info.filename} is larger than {MAX_MEMBER_BYTES // (1024 * 1024)} MB and was skipped.")
            continue
        tables[table] = _rows(raw)
        files_read.append(info.filename)

    for table in ("positions", "skills", "profile"):
        if table not in tables:
            warnings.append(f"No {table.capitalize()}.csv found; that part of the profile is empty.")
    return tables, warnings, files_read
