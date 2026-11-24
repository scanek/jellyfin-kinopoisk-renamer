#!/bin/bash
# Jellyfin / qBittorrent Post-Download Automation Wrapper
# Arguments passed by qBittorrent:
# $1 = %F (Content path: folder or file)
# $2 = %L (Category: movies or series)
# $3 = %N (Torrent name)

# Wait a short moment for qBittorrent to release open file descriptors
sleep 3

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/renamer.log"

python3 "${SCRIPT_DIR}/jellyfin_renamer.py" "$1" "$2" "$3" >> "${LOG_FILE}" 2>&1
