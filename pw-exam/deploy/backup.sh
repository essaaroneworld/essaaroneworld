#!/usr/bin/env bash
# Consistent online backup of the exam database (SQLite backup API), keeping 14 days.
set -euo pipefail
SRC="${1:-/var/lib/pwexam/pwexam.db}"
DEST_DIR="${2:-/var/backups/pwexam}"
mkdir -p "$DEST_DIR"
DEST="$DEST_DIR/pwexam-$(date +%Y%m%d-%H%M).db"
python3 - "$SRC" "$DEST" <<'PY'
import sqlite3, sys
src, dest = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dest)
dest.close(); src.close()
PY
gzip -f "$DEST"
find "$DEST_DIR" -name 'pwexam-*.db.gz' -mtime +14 -delete
echo "backup: $DEST.gz"
