#!/usr/bin/env bash
# Offline LAN exam lab: run on the lab server; candidates open the printed LAN address.
cd "$(dirname "$0")/.."
exec python3 -m pwexam --host 0.0.0.0 --port "${PWEXAM_PORT:-8080}" --data "${PWEXAM_DATA:-data/pwexam.db}" "$@"
