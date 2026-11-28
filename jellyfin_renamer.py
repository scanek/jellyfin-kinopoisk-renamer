#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jellyfin Universal Media Renamer & Organizer
Automated media organizer and renamer for Jellyfin and qBittorrent.
"""

import sys
import os
import re
import shutil
import argparse
import logging
import json
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

# Supported video and sidecar extensions
VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.ts', '.mov', '.wmv', '.webm', '.m4v'}
SUB_EXTS = {'.srt', '.ass', '.vtt', '.sub'}
COMPANION_EXTS = {'.srt', '.ass', '.vtt', '.sub', '.nfo', '.jpg', '.jpeg', '.png'}

# Release-level metadata files to keep in show/movie root (never move to Season XX)
ROOT_METADATA_FILES = {
    'folder.jpg', 'folder.png', 'folder.webp',
    'poster.jpg', 'poster.png', 'poster.webp',
    'fanart.jpg', 'fanart.png', 'fanart.webp',
    'backdrop.jpg', 'backdrop.png', 'backdrop.webp',
    'clearlogo.png', 'logo.png',
    'season01-poster.jpg', 'season02-poster.jpg',
    'tvshow.nfo'
}

# Directories to ignore in batch mode
IGNORED_DIRS = {
    'lost+found', '@eadir', '.recycle', 'incomplete', 'tor_files',
    'othermedia', 'others', 'music', 'photos', 'podcasts', 'audiobooks'
}

# Known noise tags from torrent releases
RELEASE_NOISE = [
    r'WEB-?DLRip', r'WEB-?DL', r'WEBRip', r'HDTVRip', r'HDTV', r'BDRip', r'BluRay', r'BRRip',
    r'1080p', r'720p', r'2160p', r'4[Kk]', r'x264', r'x265', r'h264', r'h265', r'HEVC', r'AVC',
    r'DDP?[0-9\.]*', r'AC3', r'AAC(?:-LC)?', r'MP3', r'DTS(?:-HD)?',
    r'Files-x', r'seleZen', r'ExKinoRay', r'Aleksan55', r'MegaPeer', r'NewStudio',
    r'LostFilm', r'HDRezka', r'Кубик в [Кк]убе', r'ColdFilm', r'BaibaKo', r'Jaskier',
    r'SDTV', r'NF', r'AMZN', r'ATVP', r'KP', r'START', r'Wink', r'Premier', r'KION', r'Okko', r'Ivi',
    r'seleZen', r'ELEKTRI4KA', r'KOSHARA', r'TBA'
]

NOISE_REGEX = re.compile(r'\b(?:' + '|'.join(RELEASE_NOISE) + r')\b|\[[^\]]+\]|\([^\)]*(?:rip|web|dl|1080|720|2160|hevc)[^\)]*\)', re.IGNORECASE)

def setup_logger(log_file=None):
    logger = logging.getLogger("jellyfin_renamer")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            logger.warning(f"Failed to create file logger at '{log_file}': {e}")

    return logger

def clean_title(title, is_folder=False):
    """Clean series/movie title preserving Russian letters, IDs, and legitimate punctuation."""
    if not title:
        return "", None

    # Preserve TVDB / TMDB tags if present (e.g. [tvdbid-12345])
    tvdb_tag = ""
    id_match = re.search(r'(\[(?:tvdbid|tmdbid)-[^\]]+\])', title, re.IGNORECASE)
    if id_match:
        tvdb_tag = id_match.group(1)

    # Handle tracker multi-title strings separated by '/'
    if "/" in title:
        parts = [p.strip() for p in title.split("/") if p.strip()]
        if parts:
            title = parts[0]

    # Remove parentheses containing season/episode info
    title = re.sub(r'\([^\)]*(?:сезон|сери|эпизод|выпуск|из\s*\d+)[^\)]*\)', ' ', title, flags=re.IGNORECASE)

    # Remove file size tags BEFORE replacing dots with spaces: 1.46Gb, 700Mb
    title = re.sub(r'\b\d+(?:[\.,]\d+)?\s*(?:[Gg][Bb]|[Mm][Bb])\b', ' ', title)

    # Strip square brackets and known noise (except preserved id tags)
    cleaned = NOISE_REGEX.sub(' ', title)

    # Strip 'rip by ...'
    cleaned = re.sub(r'rip\s+by\s+[^\s\.]+', ' ', cleaned, flags=re.IGNORECASE)

    # Extract year if in parentheses: (2026) -> year = 2026, remove "(2026)"
    year_paren_match = re.search(r'\(\s*(19\d\d|20\d\d)\s*\)', cleaned)
    year = None
    if year_paren_match:
        year = year_paren_match.group(1)
        cleaned = re.sub(r'\(\s*' + year + r'\s*\)', ' ', cleaned)
    else:
        year_match = re.search(r'\b(19\d\d|20\d\d)\b', cleaned)
        if year_match:
            year = year_match.group(1)
            cleaned = re.sub(r'\b' + year + r'\b', ' ', cleaned)

    # Replace dots between words with spaces while preserving dotted titles
    cleaned = re.sub(r'(?<=\S)\.(?=\S)', ' ', cleaned)
    cleaned = cleaned.replace('_', ' ')

    # Remove season tokens from title
    cleaned = re.sub(r'\b[Ss]\d{1,2}\b|\b\d{1,2}\s*сезон\b|\bсезон\s*\d{1,2}\b', ' ', cleaned, flags=re.IGNORECASE)

    # Remove language tags like RUS, ENG
    cleaned = re.sub(r'\b(?:RUS|ENG|UKR|DUB)\b', ' ', cleaned, flags=re.IGNORECASE)

    # Sanitize characters forbidden in filenames or path separators: / \ : * ? " < > |
    cleaned = re.sub(r'[:/\\*?"<>|]', ' ', cleaned)

    # Remove empty parentheses
    cleaned = re.sub(r'\(\s*\)', ' ', cleaned)

    # Collapse multiple spaces and trim
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' -._')

    # Re-append TVDB/TMDB tag if it was present
    if tvdb_tag:
        cleaned = f"{cleaned} {tvdb_tag}".strip()

    return cleaned, year

def load_app_config():
    cfg = {
        "kinopoisk_api_key": "",
        "jellyfin_url": "http://localhost:8096",
        "jellyfin_api_key": "",
        "puid": None,
        "pgid": None,
        "action": "move",
        "rename_folders": True
    }
    candidates = [
        os.environ.get("RENAME_CONFIG_FILE", ""),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
        "/config/scripts/config.json",
        "/volume1/docker/qbit/config/scripts/config.json",
        "./config.json"
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    cfg.update(json.load(f))
                break
            except Exception:
                pass

    if os.environ.get("KINOPOISK_API_KEY"):
        cfg["kinopoisk_api_key"] = os.environ["KINOPOISK_API_KEY"]
    if os.environ.get("JELLYFIN_URL"):
        cfg["jellyfin_url"] = os.environ["JELLYFIN_URL"]
    if os.environ.get("JELLYFIN_API_KEY"):
        cfg["jellyfin_api_key"] = os.environ["JELLYFIN_API_KEY"]
    return cfg

APP_CONFIG = load_app_config()
KINOPOISK_API_KEY = APP_CONFIG.get("kinopoisk_api_key", "")
_kp_cache = None

def get_cache_file():
    for candidate in [
        "/config/scripts/kp_cache.json",
        "/volume1/docker/qbit/config/scripts/kp_cache.json",
        "/volume1/script/kp_cache.json"
    ]:
        if os.path.exists(os.path.dirname(candidate)):
            return candidate
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "kp_cache.json")

def load_kp_cache():
    global _kp_cache
    if _kp_cache is not None:
        return _kp_cache
    cache_path = get_cache_file()
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                _kp_cache = json.load(f)
                return _kp_cache
        except Exception:
            pass
    _kp_cache = {}
    return _kp_cache

def save_kp_cache():
    global _kp_cache
    if _kp_cache is None:
        return
    cache_path = get_cache_file()
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(_kp_cache, f, ensure_ascii=False, indent=2)
        ensure_permissions(cache_path, is_dir=False)
    except Exception:
        pass

def score_film(f, query, year=None, category=None):
    score = 0
    name_ru = (f.get("nameRu") or "").strip()
    name_en = (f.get("nameEn") or "").strip()
    f_type = (f.get("type") or "").upper()
    f_year = (f.get("year") or "").strip()
    q_clean = query.lower().strip()

    # Category preference
    if category == "series":
        if f_type in ("TV_SERIES", "MINI_SERIES", "TV_SHOW"):
            score += 35
        elif f_type in ("FILM", "VIDEO"):
            score -= 20
    elif category == "movies":
        if f_type in ("FILM", "VIDEO"):
            score += 35
        elif f_type in ("TV_SERIES", "MINI_SERIES"):
            score -= 20

    # Title match
    if name_en and name_en.lower() == q_clean:
        score += 50
    elif name_ru and name_ru.lower() == q_clean:
        score += 50
    elif name_en and (name_en.lower().startswith(q_clean) or q_clean in name_en.lower()):
        score += 20
    elif name_ru and (name_ru.lower().startswith(q_clean) or q_clean in name_ru.lower()):
        score += 20

    # Year match
    if year and f_year and f_year.isdigit() and str(year).isdigit():
        diff = abs(int(f_year) - int(year))
        if diff == 0:
            score += 40
        elif diff == 1:
            score += 20
        elif diff <= 3:
            score += 5
        else:
            score -= 15

    votes = f.get("ratingVoteCount") or 0
    if isinstance(votes, int) and votes > 1000:
        score += 5

    return score

def kinopoisk_resolve(query, year=None, category=None, api_key=None, logger=None):
    if not query:
        return query, year

    # Skip if already has Cyrillic
    if re.search(r'[а-яА-ЯёЁ]', query):
        return query, year

    api_key = api_key or KINOPOISK_API_KEY
    if not api_key:
        return query, year

    cache = load_kp_cache()
    cache_key = f"{category or 'all'}:{query.lower().strip()}:{year or ''}"
    if cache_key in cache:
        entry = cache[cache_key]
        return entry.get("nameRu", query), entry.get("year", year)

    clean_q = re.sub(r'\[(?:tvdbid|tmdbid|imdbid)-[^\]]+\]', '', query, flags=re.IGNORECASE).strip()
    if not clean_q:
        return query, year

    url = f"https://kinopoiskapiunofficial.tech/api/v2.1/films/search-by-keyword?keyword={urllib.parse.quote(clean_q)}"
    req = urllib.request.Request(url, headers={"X-API-KEY": api_key, "User-Agent": "JellyfinRenamer/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            films = data.get("films", [])
            if not films:
                cache[cache_key] = {"nameRu": query, "year": year}
                save_kp_cache()
                return query, year

            scored = [(score_film(f, clean_q, year, category), f) for f in films]
            scored.sort(key=lambda x: x[0], reverse=True)
            best_score, best_f = scored[0]

            name_ru = best_f.get("nameRu")
            if name_ru:
                name_ru = name_ru.replace(':', ' - ')
                name_ru = re.sub(r'[\/\\*?"<>|]', '', name_ru)
                name_ru = re.sub(r'\s+', ' ', name_ru).strip()

                kp_year = best_f.get("year")
                final_year = kp_year if (kp_year and kp_year.isdigit()) else year

                cache[cache_key] = {"nameRu": name_ru, "year": final_year}
                save_kp_cache()

                if logger:
                    logger.info(f"Kinopoisk match: '{clean_q}' -> '{name_ru}' ({final_year}) [score={best_score}]")

                return name_ru, final_year
    except Exception as e:
        if logger:
            logger.warning(f"Kinopoisk API lookup failed for '{clean_q}': {e}")

    return query, year

def resolve_title(raw_name, is_folder=False, category=None, use_kp=True, api_key=None, logger=None):
    clean_name, year = clean_title(raw_name, is_folder=is_folder)

    tvdb_tag = ""
    m_tag = re.search(r'(\[(?:tvdbid|tmdbid|imdbid)-[^\]]+\])', clean_name, re.IGNORECASE)
    if m_tag:
        tvdb_tag = m_tag.group(1)
        clean_name = clean_name.replace(tvdb_tag, '').strip()

    if use_kp and clean_name and not re.search(r'[а-яА-ЯёЁ]', clean_name):
        kp_name, kp_year = kinopoisk_resolve(clean_name, year=year, category=category, api_key=api_key, logger=logger)
        if kp_name and kp_name != clean_name:
            clean_name = kp_name
            if kp_year:
                year = kp_year

    return clean_name, year, tvdb_tag

def extract_season_episode(filename, folder_name="", torrent_name=""):
    """
    Extracts season and episode number from filename, folder, or torrent name.
    Supports S01E02, 1x02, Russian '01 серия', '10. Папины дочки', Cyrillic 'с01э02'.
    """
    season = None
    episode = None

    stem = os.path.splitext(filename)[0]

    # Standard notation: S01E02, s1e2, S01E01-E02
    m = re.search(r'[Ss](\d{1,2})[._\s-]*[Ee](\d{1,3})(?:-[Ee]?\d{1,3})?', stem, re.IGNORECASE)
    if m:
        season = int(m.group(1))
        episode = int(m.group(2))
        return season, episode

    # Cyrillic notation: C01E02
    m = re.search(r'[Сс](\d{1,2})[._\s-]*[Ээ](\d{1,3})', stem, re.IGNORECASE)
    if m:
        season = int(m.group(1))
        episode = int(m.group(2))
        return season, episode

    # Alternate notation: 1x02
    m = re.search(r'\b(\d{1,2})x(\d{1,3})\b', stem, re.IGNORECASE)
    if m:
        season = int(m.group(1))
        episode = int(m.group(2))
        return season, episode

    # Text notation: 'серия XX', 'выпуск XX'
    m = re.search(r'(\d{1,3})\s*(?:серия|эпизод|выпуск)', stem, re.IGNORECASE)
    if m:
        episode = int(m.group(1))
    else:
        m = re.search(r'(?:серия|эпизод|выпуск)\s*(\d{1,3})', stem, re.IGNORECASE)
        if m:
            episode = int(m.group(1))

    # Leading episode number: '01. Title'
    if episode is None:
        m = re.match(r'^\s*(\d{1,3})\s*[\.\-_\s]', stem)
        if m:
            episode = int(m.group(1))

    # Now resolve season if missing from filename
    if season is None:
        targets = [folder_name, torrent_name, stem]
        for target in targets:
            if not target:
                continue
            sm = re.search(r'\b[Ss](\d{1,2})\b', target)
            if sm:
                season = int(sm.group(1))
                break
            sm = re.search(r'(\d{1,2})\s*сезон', target, re.IGNORECASE)
            if sm:
                season = int(sm.group(1))
                break
            sm = re.search(r'сезон\s*(\d{1,2})', target, re.IGNORECASE)
            if sm:
                season = int(sm.group(1))
                break
            sm = re.search(r'дело\s*№?\s*(\d{1,2})', target, re.IGNORECASE)
            if sm:
                season = int(sm.group(1))
                break

    if season is None:
        season = 1

    return season, episode

def ensure_permissions(path, is_dir=False):
    """
    Ensures that path has full read/write permissions (0o777 for dirs, 0o666 for files)
    and inherits UID/GID from parent directory if parent exists.
    """
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if os.path.exists(parent):
            st = os.stat(parent)
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                os.chown(path, st.st_uid, st.st_gid)
    except Exception:
        pass

    try:
        if is_dir:
            os.chmod(path, 0o777)
        else:
            os.chmod(path, 0o666)
    except Exception:
        pass

def makedirs_with_perms(path):
    """Recursively creates directory with 0o777 and inherits parent UID/GID."""
    if os.path.exists(path):
        ensure_permissions(path, is_dir=True)
        return

    parent = os.path.dirname(path)
    if parent and parent != path and not os.path.exists(parent):
        makedirs_with_perms(parent)

    os.makedirs(path, exist_ok=True)
    ensure_permissions(path, is_dir=True)

def apply_permissions_recursively(target_dir):
    """Applies parent UID/GID and 777/666 to target_dir and all its contents recursively."""
    try:
        target_dir = os.path.abspath(target_dir)
        parent = os.path.dirname(target_dir)
        target_uid = 100
        target_gid = 101
        if os.path.exists(parent):
            st = os.stat(parent)
            target_uid, target_gid = st.st_uid, st.st_gid

        is_root = (os.geteuid() == 0) if hasattr(os, "geteuid") else False

        for root, dirs, files in os.walk(target_dir):
            if is_root:
                try:
                    os.chown(root, target_uid, target_gid)
                except OSError:
                    pass
            try:
                os.chmod(root, 0o777)
            except OSError:
                pass

            for f in files:
                f_path = os.path.join(root, f)
                if is_root:
                    try:
                        os.chown(f_path, target_uid, target_gid)
                    except OSError:
                        pass
                try:
                    os.chmod(f_path, 0o666)
                except OSError:
                    pass
    except Exception:
        pass

def process_file_action(src, dst, action="move", dry_run=False, logger=None):
    """Executes move, hardlink, or copy safely with dry-run support."""
    if os.path.abspath(src) == os.path.abspath(dst):
        return True

    if dry_run:
        if logger:
            logger.info(f"[DRY-RUN] Would {action.upper()}:")
            logger.info(f"  FROM: {src}")
            logger.info(f"    TO: {dst}")
        return True

    dst_dir = os.path.dirname(dst)
    makedirs_with_perms(dst_dir)

    if os.path.exists(dst):
        if os.path.samefile(src, dst):
            return True
        os.remove(dst)

    try:
        if action == "move":
            shutil.move(src, dst)
        elif action == "hardlink":
            os.link(src, dst)
        elif action == "copy":
            shutil.copy2(src, dst)
        else:
            shutil.move(src, dst)

        ensure_permissions(dst, is_dir=False)

        if logger:
            logger.info(f"SUCCESS ({action}): {os.path.basename(src)} -> {os.path.basename(dst)}")
        return True
    except Exception as e:
        if logger:
            logger.error(f"FAILED to {action} '{src}' -> '{dst}': {e}")
        return False

def rename_or_merge_directory(src_dir, dst_dir, dry_run=False, logger=None):
    """Renames or merges directory if target already exists."""
    src_dir = os.path.abspath(src_dir)
    dst_dir = os.path.abspath(dst_dir)

    if src_dir == dst_dir:
        return dst_dir

    if dry_run:
        if os.path.exists(dst_dir):
            if logger:
                logger.info(f"[DRY-RUN] Would MERGE FOLDER:")
                logger.info(f"  FROM: {src_dir}")
                logger.info(f"  INTO EXISTING: {dst_dir}")
        else:
            if logger:
                logger.info(f"[DRY-RUN] Would RENAME FOLDER:")
                logger.info(f"  FROM: {src_dir}")
                logger.info(f"    TO: {dst_dir}")
        return dst_dir

    try:
        if not os.path.exists(dst_dir):
            os.rename(src_dir, dst_dir)
            apply_permissions_recursively(dst_dir)
            if logger:
                logger.info(f"SUCCESS (rename folder): '{os.path.basename(src_dir)}' -> '{os.path.basename(dst_dir)}'")
            return dst_dir
        else:
            if logger:
                logger.info(f"Merging folder '{os.path.basename(src_dir)}' into existing '{os.path.basename(dst_dir)}'...")
            for root, dirs, files in os.walk(src_dir, topdown=False):
                rel_p = os.path.relpath(root, src_dir)
                dest_root = dst_dir if rel_p == '.' else os.path.join(dst_dir, rel_p)
                makedirs_with_perms(dest_root)

                for f in files:
                    s_f = os.path.join(root, f)
                    d_f = os.path.join(dest_root, f)
                    if os.path.exists(d_f):
                        if not os.path.samefile(s_f, d_f):
                            os.remove(d_f)
                            shutil.move(s_f, d_f)
                    else:
                        shutil.move(s_f, d_f)
                    ensure_permissions(d_f, is_dir=False)

                for d in dirs:
                    d_path = os.path.join(root, d)
                    try:
                        os.rmdir(d_path)
                    except OSError:
                        pass

            try:
                os.rmdir(src_dir)
            except OSError:
                pass

            apply_permissions_recursively(dst_dir)
            if logger:
                logger.info(f"SUCCESS (merged): '{os.path.basename(src_dir)}' -> '{os.path.basename(dst_dir)}'")
            return dst_dir

    except Exception as e:
        if logger:
            logger.error(f"FAILED to rename/merge directory '{src_dir}' -> '{dst_dir}': {e}")
        return src_dir

def is_already_organized_series(dir_path, full_show_name=None):
    """Checks if a series directory is already fully organized into Season XX folders and clean folder name."""
    has_season_folders = False
    has_unorganized_videos = False
    has_mismatched_videos = False

    for item in os.listdir(dir_path):
        full_p = os.path.join(dir_path, item)
        if os.path.isdir(full_p) and re.match(r'^Season\s+\d{2}$', item, re.IGNORECASE):
            has_season_folders = True
            if full_show_name:
                for f in os.listdir(full_p):
                    ext = os.path.splitext(f)[1].lower()
                    if ext in VIDEO_EXTS:
                        if not f.startswith(f"{full_show_name} - S"):
                            has_mismatched_videos = True
        elif os.path.isfile(full_p):
            ext = os.path.splitext(item)[1].lower()
            if ext in VIDEO_EXTS:
                has_unorganized_videos = True

    return has_season_folders and not has_unorganized_videos and not has_mismatched_videos

def process_series_directory(dir_path, torrent_name="", action="move", dry_run=False, rename_folder=True, use_kp=True, api_key=None, logger=None):
    """Processes a TV show directory: organizes into Season XX, renames files, and cleans folder name."""
    dir_path = os.path.abspath(dir_path)
    folder_name = os.path.basename(dir_path)

    # Determine series title
    clean_show, year, tvdb_tag = resolve_title(torrent_name or folder_name, is_folder=True, category="series", use_kp=use_kp, api_key=api_key, logger=logger)
    if not clean_show:
        clean_show, year, tvdb_tag = resolve_title(folder_name, is_folder=True, category="series", use_kp=use_kp, api_key=api_key, logger=logger)

    base_show_name = f"{clean_show} ({year})" if year and f"({year})" not in clean_show else clean_show
    full_show_name = f"{base_show_name} {tvdb_tag}".strip() if tvdb_tag else base_show_name

    # Check if folder name needs renaming
    parent_dir = os.path.dirname(dir_path)
    target_show_dir = os.path.join(parent_dir, full_show_name) if rename_folder else dir_path

    # Check if already organized
    if is_already_organized_series(dir_path, full_show_name=full_show_name):
        if rename_folder and folder_name != full_show_name and not NOISE_REGEX.search(full_show_name):
            if logger:
                logger.info(f"Series files already organized. Checking folder rename for: '{folder_name}' -> '{full_show_name}'")
            final_dir = rename_or_merge_directory(dir_path, target_show_dir, dry_run=dry_run, logger=logger)
        else:
            final_dir = dir_path
            if logger:
                logger.info(f"Skipping already organized series: '{folder_name}'")
        if not dry_run and os.path.exists(final_dir):
            apply_permissions_recursively(final_dir)
        return

    if logger:
        logger.info(f"Processing Series Directory: {dir_path}")
        logger.info(f"Identified Show Name: '{full_show_name}'")

    # Scan for video files directly in dir_path
    all_entries = os.listdir(dir_path)
    video_files = []

    for entry in all_entries:
        full_p = os.path.join(dir_path, entry)
        if os.path.isfile(full_p):
            ext = os.path.splitext(entry)[1].lower()
            if ext in VIDEO_EXTS:
                video_files.append((entry, dir_path))

    # If no video in root, search in subfolders (including Season XX if filenames don't match canonical format)
    if not video_files:
        for root, dirs, files in os.walk(dir_path):
            if root == dir_path:
                continue
            is_season = bool(re.match(r'^Season\s+\d{2}$', os.path.basename(root), re.IGNORECASE))
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in VIDEO_EXTS:
                    if not is_season or not f.startswith(f"{full_show_name} - S"):
                        video_files.append((f, root))

    if not video_files:
        if logger:
            logger.info(f"No unorganized video files in '{dir_path}'")
        if rename_folder and folder_name != full_show_name:
            rename_or_merge_directory(dir_path, target_show_dir, dry_run=dry_run, logger=logger)
        return

    # Process each video file and its companions
    for video_filename, src_dir in sorted(video_files, key=lambda x: x[0]):
        video_path = os.path.join(src_dir, video_filename)
        video_stem, video_ext = os.path.splitext(video_filename)

        season_num, ep_num = extract_season_episode(video_filename, folder_name=folder_name, torrent_name=torrent_name)

        if ep_num is None:
            if logger:
                logger.warning(f"Could not determine episode number for: {video_filename}")
            continue

        season_folder_name = f"Season {season_num:02d}"
        target_dir = os.path.join(dir_path, season_folder_name)

        new_video_filename = f"{full_show_name} - S{season_num:02d}E{ep_num:02d}{video_ext}"
        target_video_path = os.path.join(target_dir, new_video_filename)

        process_file_action(video_path, target_video_path, action=action, dry_run=dry_run, logger=logger)

        # Look for companion files (subtitles, thumbs, nfo)
        for sibling in os.listdir(src_dir):
            if sibling == video_filename or sibling.lower() in ROOT_METADATA_FILES:
                continue

            sib_stem, sib_ext = os.path.splitext(sibling)
            sib_ext_lower = sib_ext.lower()

            if sib_ext_lower not in COMPANION_EXTS:
                continue

            if sib_stem.startswith(video_stem) or video_stem in sib_stem:
                suffix = sib_stem[len(video_stem):] if sib_stem.startswith(video_stem) else ""
                
                if '-thumb' in sibling.lower():
                    new_sib_filename = f"{full_show_name} - S{season_num:02d}E{ep_num:02d}-thumb{sib_ext}"
                elif '.ru' in sibling.lower() or 'rus' in sibling.lower():
                    new_sib_filename = f"{full_show_name} - S{season_num:02d}E{ep_num:02d}.ru{sib_ext}"
                else:
                    new_sib_filename = f"{full_show_name} - S{season_num:02d}E{ep_num:02d}{suffix}{sib_ext}"

                sib_src = os.path.join(src_dir, sibling)
                sib_dst = os.path.join(target_dir, new_sib_filename)
                process_file_action(sib_src, sib_dst, action=action, dry_run=dry_run, logger=logger)

    # Finally, rename/merge the parent series directory to clean show name
    if rename_folder and folder_name != full_show_name:
        final_dir = rename_or_merge_directory(dir_path, target_show_dir, dry_run=dry_run, logger=logger)
    else:
        final_dir = dir_path

    if not dry_run and os.path.exists(final_dir):
        apply_permissions_recursively(final_dir)

def process_movie_item(path, torrent_name="", action="move", dry_run=False, rename_folder=True, use_kp=True, api_key=None, logger=None):
    """
    Processes a movie item.
    Always ensures the movie gets its own dedicated folder: 'Movie Name (Year)/Movie Name (Year).ext'.
    """
    path = os.path.abspath(path)

    if os.path.isfile(path):
        ext = os.path.splitext(path)[1].lower()
        if ext not in VIDEO_EXTS:
            return

        movie_dir = os.path.dirname(path)
        filename = os.path.basename(path)
        stem = os.path.splitext(filename)[0]

        clean_name, year, tag = resolve_title(torrent_name or stem, is_folder=False, category="movies", use_kp=use_kp, api_key=api_key, logger=logger)
        base_name = f"{clean_name} ({year})" if year and f"({year})" not in clean_name else clean_name
        new_base = f"{base_name} {tag}".strip() if tag else base_name
        new_filename = f"{new_base}{ext}"

        # If file is loose in library root, create a dedicated folder for it:
        if is_library_root(movie_dir) or os.path.basename(movie_dir).lower() in ('movies', 'фильмы'):
            target_movie_dir = os.path.join(movie_dir, new_base)
        else:
            target_movie_dir = movie_dir

        target_path = os.path.join(target_movie_dir, new_filename)
        process_file_action(path, target_path, action=action, dry_run=dry_run, logger=logger)

        # Look for companion files (posters, nfo, subtitles) and move into movie folder
        for sibling in os.listdir(movie_dir):
            if sibling == filename:
                continue
            sib_path = os.path.join(movie_dir, sibling)
            if not os.path.isfile(sib_path):
                continue

            sib_stem, sib_ext = os.path.splitext(sibling)
            if sib_stem.startswith(stem) or stem in sib_stem:
                suffix = sib_stem[len(stem):] if sib_stem.startswith(stem) else ""
                if 'poster' in sib_stem.lower():
                    suffix = "-poster"
                elif 'fanart' in sib_stem.lower() or 'backdrop' in sib_stem.lower():
                    suffix = "-fanart"
                new_sib = f"{new_base}{suffix}{sib_ext}"
                process_file_action(sib_path, os.path.join(target_movie_dir, new_sib), action=action, dry_run=dry_run, logger=logger)

        if not dry_run and os.path.exists(target_movie_dir):
            apply_permissions_recursively(target_movie_dir)

    elif os.path.isdir(path):
        folder_name = os.path.basename(path)
        clean_name, year, tag = resolve_title(torrent_name or folder_name, is_folder=True, category="movies", use_kp=use_kp, api_key=api_key, logger=logger)
        base_name = f"{clean_name} ({year})" if year and f"({year})" not in clean_name else clean_name
        new_base = f"{base_name} {tag}".strip() if tag else base_name

        if logger:
            logger.info(f"Processing Movie Directory: {path} -> Target Base: '{new_base}'")

        for item in os.listdir(path):
            full_p = os.path.join(path, item)
            if os.path.isfile(full_p):
                ext = os.path.splitext(item)[1].lower()
                if ext in VIDEO_EXTS:
                    new_item_name = f"{new_base}{ext}"
                    process_file_action(full_p, os.path.join(path, new_item_name), action=action, dry_run=dry_run, logger=logger)
                elif ext in COMPANION_EXTS:
                    stem = os.path.splitext(item)[0]
                    suffix = ""
                    if 'poster' in stem.lower():
                        suffix = "-poster"
                    elif 'fanart' in stem.lower() or 'backdrop' in stem.lower():
                        suffix = "-fanart"
                    new_item_name = f"{new_base}{suffix}{ext}" if suffix else item
                    process_file_action(full_p, os.path.join(path, new_item_name), action=action, dry_run=dry_run, logger=logger)

        # Rename parent folder if needed
        if rename_folder and folder_name != new_base:
            parent_dir = os.path.dirname(path)
            target_movie_dir = os.path.join(parent_dir, new_base)
            final_dir = rename_or_merge_directory(path, target_movie_dir, dry_run=dry_run, logger=logger)
        else:
            final_dir = path

        if not dry_run and os.path.exists(final_dir):
            apply_permissions_recursively(final_dir)

def is_library_root(target_path):
    """Detects if target_path is a media library root containing multiple items."""
    if not os.path.isdir(target_path):
        return False

    norm = target_path.lower().rstrip('/\\').replace('\\', '/')
    base = os.path.basename(norm)
    if base in ('series', 'movies', 'сериалы', 'фильмы'):
        return True

    subdirs = []
    direct_videos = []

    for item in os.listdir(target_path):
        p = os.path.join(target_path, item)
        if os.path.isdir(p):
            if item.lower() not in IGNORED_DIRS and not item.startswith('.'):
                subdirs.append(item)
        elif os.path.isfile(p):
            ext = os.path.splitext(item)[1].lower()
            if ext in VIDEO_EXTS:
                direct_videos.append(item)

    # If it contains Season XX subfolders, it is a TV show folder, NOT a library root!
    if any(re.match(r'^(?:Season\s+\d{1,2}|Спецматериалы|Specials)$', d, re.I) for d in subdirs):
        return False

    # If it contains direct video files with episode markers, it is a TV show folder, NOT a library root!
    if direct_videos:
        has_episodes = any(extract_season_episode(f)[1] is not None for f in direct_videos)
        if has_episodes:
            return False

    # A library root typically has 2 or more subdirectories representing different shows/movies
    if len(subdirs) >= 2:
        return True

    return False

def process_batch(root_dir, category, action="move", dry_run=False, rename_folder=True, use_kp=True, api_key=None, logger=None):
    """Batch-processes all items inside a media library root."""
    root_dir = os.path.abspath(root_dir)
    logger.info(f">>> BATCH MODE ACTIVATED for: {root_dir} (Category: {category}) <<<")

    entries = sorted(os.listdir(root_dir))
    total_processed = 0

    if category == "series":
        for item in entries:
            if item.lower() in IGNORED_DIRS or item.startswith('.'):
                continue
            item_path = os.path.join(root_dir, item)
            if os.path.isdir(item_path):
                total_processed += 1
                logger.info("-" * 40)
                logger.info(f"[{total_processed}] Batch Series Item: {item}")
                process_series_directory(item_path, action=action, dry_run=dry_run, rename_folder=rename_folder, use_kp=use_kp, api_key=api_key, logger=logger)

    elif category == "movies":
        for item in entries:
            if item.lower() in IGNORED_DIRS or item.startswith('.'):
                continue
            item_path = os.path.join(root_dir, item)
            if os.path.isdir(item_path):
                total_processed += 1
                logger.info("-" * 40)
                logger.info(f"[{total_processed}] Batch Movie Directory: {item}")
                process_movie_item(item_path, action=action, dry_run=dry_run, rename_folder=rename_folder, use_kp=use_kp, api_key=api_key, logger=logger)
            elif os.path.isfile(item_path):
                ext = os.path.splitext(item)[1].lower()
                if ext in VIDEO_EXTS:
                    total_processed += 1
                    logger.info("-" * 40)
                    logger.info(f"[{total_processed}] Batch Movie File: {item}")
                    process_movie_item(item_path, action=action, dry_run=dry_run, rename_folder=rename_folder, use_kp=use_kp, api_key=api_key, logger=logger)

    logger.info("=" * 60)
    logger.info(f"Batch completed: processed {total_processed} items.")
    logger.info("=" * 60)

def trigger_jellyfin_refresh(logger=None):
    """Triggers Jellyfin library scan using configured jellyfin_url and jellyfin_api_key."""
    cfg = load_app_config()
    token = cfg.get("jellyfin_api_key", "").strip()
    if not token:
        if logger:
            logger.debug("Jellyfin API key not configured; skipping library refresh.")
        return False

    base_url = cfg.get("jellyfin_url", "http://localhost:8096").strip().rstrip("/")
    if not base_url:
        return False

    parsed = urllib.parse.urlparse(base_url)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "localhost"
    port_str = f":{parsed.port}" if parsed.port else ""

    urls = [f"{base_url}/Library/Refresh"]

    # When running inside Docker and target is localhost/127.0.0.1, attempt host gateway fallbacks on the same port
    if host in ("localhost", "127.0.0.1") and os.path.exists("/.dockerenv"):
        for fallback_host in ["host.docker.internal", "172.17.0.1"]:
            fallback_url = f"{scheme}://{fallback_host}{port_str}/Library/Refresh"
            if fallback_url not in urls:
                urls.append(fallback_url)

    for url in urls:
        try:
            req = urllib.request.Request(url, method="POST")
            req.add_header("Authorization", f'MediaBrowser Client="qBittorrent-Renamer", Device="Renamer", DeviceId="renamer-service", Version="1.0.0", Token="{token}"')
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status in (200, 204):
                    if logger:
                        logger.info(f"Jellyfin library refresh triggered successfully via {url}")
                    return True
        except Exception:
            continue

    if logger:
        logger.warning(f"Could not refresh Jellyfin library at {base_url}/Library/Refresh")
    return False

def resolve_concatenated_path(target_path):
    """
    Attempts to recover a valid path if arguments were accidentally concatenated,
    such as qBittorrent '%F%L%N' missing spaces/quotes.
    """
    if os.path.exists(target_path):
        return target_path

    check = target_path
    existing_dir = None
    while check and check != os.path.dirname(check):
        check = os.path.dirname(check)
        if os.path.isdir(check):
            existing_dir = check
            break

    if not existing_dir:
        return target_path

    candidates = []
    target_norm = os.path.normpath(os.path.abspath(target_path))
    try:
        for entry in os.scandir(existing_dir):
            entry_path = os.path.normpath(os.path.abspath(entry.path))
            if target_norm.startswith(entry_path) and len(entry_path) < len(target_norm):
                candidates.append(entry.path)
    except Exception:
        pass

    if candidates:
        candidates.sort(key=lambda x: len(x), reverse=True)
        return candidates[0]

    return target_path

def main():
    argv = sys.argv[1:]

    dry_run = "--dry-run" in argv
    if dry_run:
        argv = [a for a in argv if a != "--dry-run"]

    batch_mode = "--all" in argv or "-a" in argv
    if batch_mode:
        argv = [a for a in argv if a not in ("--all", "-a")]

    no_rename_folder = "--no-rename-folder" in argv
    if no_rename_folder:
        argv = [a for a in argv if a != "--no-rename-folder"]
    rename_folder = not no_rename_folder

    use_kp = True
    if "--no-kp" in argv:
        use_kp = False
        argv = [a for a in argv if a != "--no-kp"]

    api_key = KINOPOISK_API_KEY
    action = "move"
    new_argv = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--action" and i + 1 < len(argv):
            action = argv[i + 1]
            i += 2
        elif a.startswith("--action="):
            action = a.split("=", 1)[1]
            i += 1
        elif a == "--kp-key" and i + 1 < len(argv):
            api_key = argv[i + 1]
            i += 2
        elif a.startswith("--kp-key="):
            api_key = a.split("=", 1)[1]
            i += 1
        elif a == "--log-file" and i + 1 < len(argv):
            log_file = argv[i + 1]
            i += 2
        elif a.startswith("--log-file="):
            log_file = a.split("=", 1)[1]
            i += 1
        else:
            new_argv.append(a)
            i += 1

    argv = new_argv

    if "--help" in argv or "-h" in argv:
        print("""Jellyfin Universal Media Renamer
Usage:
  python3 jellyfin_renamer.py [OPTIONS] <PATH> [CATEGORY] [TORRENT_NAME]

Examples:
  python3 jellyfin_renamer.py --dry-run /path/to/movies
  python3 jellyfin_renamer.py /path/to/movies
  python3 jellyfin_renamer.py --dry-run /path/to/series
  python3 jellyfin_renamer.py /path/to/series
  python3 jellyfin_renamer.py "/path/to/release_folder"

Options:
  --dry-run             Simulate changes without modifying files
  --no-kp               Disable Kinopoisk search
  --no-rename-folder    Do not rename parent release directory
  --action {move,copy,hardlink}
                        Action to perform (default: move)
  --kp-key KEY          Override Kinopoisk API key
  --log-file PATH       Custom log file path
  -h, --help            Show this help message
""")
        sys.exit(0)

    if not argv:
        print("Usage: jellyfin_renamer.py [--dry-run] [--all] [--no-kp] [--kp-key KEY] [--no-rename-folder] [--action {move,hardlink,copy}] [--log-file PATH] <path> [category] [torrent_name]")
        sys.exit(1)

    target_path = argv[0].strip('"\'')
    category = argv[1].strip('"\'').lower() if len(argv) > 1 else ""
    torrent_name = argv[2].strip('"\'') if len(argv) > 2 else ""

    log_file = None
    for cand in ["/config/scripts/renamer.log", os.path.join(os.path.dirname(os.path.abspath(__file__)), "renamer.log")]:
        d = os.path.dirname(os.path.abspath(cand))
        if os.path.exists(d) and os.access(d, os.W_OK):
            log_file = cand
            break

    logger = setup_logger(log_file)

    logger.info("=" * 60)
    logger.info(f"Jellyfin Renamer Invoked")
    logger.info(f"Target Path: {target_path}")
    logger.info(f"Category: {category}")
    logger.info(f"Torrent Name: {torrent_name}")
    logger.info(f"Action: {action} | Dry Run: {dry_run} | Rename Folders: {rename_folder} | Kinopoisk: {use_kp}")
    logger.info("=" * 60)

    if not os.path.exists(target_path):
        recovered = resolve_concatenated_path(target_path)
        if recovered != target_path and os.path.exists(recovered):
            logger.warning(f"Target path '{target_path}' did not exist, but recovered valid path: '{recovered}'")
            target_path = recovered
        else:
            logger.error(f"Target path does not exist: {target_path}")
            sys.exit(1)

    # Auto-detect category if empty
    if not category:
        norm_path = target_path.lower().replace('\\', '/')
        if '/series' in norm_path or 'сериал' in norm_path:
            category = "series"
        elif '/movies' in norm_path or 'фильм' in norm_path:
            category = "movies"
        else:
            category = "series"

    logger.info(f"Effective Category: {category}")

    # Check if we should run in batch mode
    if batch_mode or is_library_root(target_path):
        process_batch(target_path, category=category, action=action, dry_run=dry_run, rename_folder=rename_folder, use_kp=use_kp, api_key=api_key, logger=logger)
    else:
        # Single item mode
        if category == "series":
            if os.path.isdir(target_path):
                process_series_directory(target_path, torrent_name=torrent_name, action=action, dry_run=dry_run, rename_folder=rename_folder, use_kp=use_kp, api_key=api_key, logger=logger)
            else:
                parent_dir = os.path.dirname(target_path)
                process_series_directory(parent_dir, torrent_name=torrent_name, action=action, dry_run=dry_run, rename_folder=rename_folder, use_kp=use_kp, api_key=api_key, logger=logger)
        elif category == "movies":
            process_movie_item(target_path, torrent_name=torrent_name, action=action, dry_run=dry_run, rename_folder=rename_folder, use_kp=use_kp, api_key=api_key, logger=logger)
        else:
            logger.warning(f"Unknown or ignored category: '{category}'. Skipping.")

    if not dry_run:
        trigger_jellyfin_refresh(logger=logger)

    logger.info("Processing complete.\n")

if __name__ == "__main__":
    main()
