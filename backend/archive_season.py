"""Archive prior-season history files.

This runs daily in the collect-results workflow, so it must be safe to run
every day: it only touches history files stamped with a *previous* year.
The old version zipped and then deleted the ENTIRE data/history directory on
every run -- current season included -- which would have wiped the model's
calibration/precision history the moment the workflow started committing
those deletions (it only survived because the commit step never staged
data/history removals).
"""
import re
import zipfile
from pathlib import Path
from datetime import datetime

YEAR_RE = re.compile(r"(\d{4})-\d{2}-\d{2}")


def archive_season(current_year=None):
    if current_year is None:
        current_year = datetime.now().year

    history_dir = Path("data/history")
    archive_dir = Path("data/archives")

    if not history_dir.exists():
        print("No history directory found")
        return

    # Bucket files by the season (year) embedded in their filename;
    # anything without a parseable date is left alone.
    by_year = {}
    for file in history_dir.glob("*.json"):
        m = YEAR_RE.search(file.name)
        if not m:
            continue
        year = int(m.group(1))
        if year < current_year:
            by_year.setdefault(year, []).append(file)

    if not by_year:
        print("No prior-season history files to archive")
        return

    archive_dir.mkdir(exist_ok=True)
    for year, files in sorted(by_year.items()):
        zip_path = archive_dir / f"season_{year}.zip"
        print(f"Archiving {len(files)} files from season {year} to {zip_path}")
        # Append into an existing archive if one already exists (e.g. a
        # straggler file discovered after the first archive run).
        with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as zf:
            existing = set(zf.namelist())
            for file in files:
                if file.name not in existing:
                    zf.write(file, file.name)
        for file in files:
            file.unlink()
        print(f"Season {year} archived and cleared from data/history.")


if __name__ == "__main__":
    archive_season()
