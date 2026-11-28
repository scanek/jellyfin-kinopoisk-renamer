#!/bin/bash
# Jellyfin / qBittorrent Post-Download Automation Wrapper
# Arguments passed by qBittorrent:
# $1 = %F (Content path: folder or file)
# $2 = %L (Category: movies or series)
# $3 = %N (Torrent name)

# Wait a short moment for qBittorrent to release open file descriptors
sleep 3

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/config/scripts/renamer.log"

if [ ! -d "/config/scripts" ]; then
    LOG_FILE="${SCRIPT_DIR}/renamer.log"
fi

# Note: jellyfin_renamer.py already writes to renamer.log via its own FileHandler.
# Discard stdout to prevent duplicate log lines, and keep stderr for unexpected errors.
python3 "${SCRIPT_DIR}/jellyfin_renamer.py" "$1" "$2" "$3" 1>/dev/null 2>> "${LOG_FILE}"
