import discord
from discord import app_commands
import subprocess
import os
import re
import json
import shutil
import asyncio
import urllib.request
import urllib.parse
import a2s
from datetime import datetime
from dotenv import load_dotenv

# A3Bot — the one bot for the Arma 3 server: service management (start/stop/
# restart/status/uptime/players), setup wizard, and backup/restore profiles.
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env_setup"))


def _config_error(message: str) -> SystemExit:
    """A bad .env_setup should say what to fix — a traceback in a3bot_stderr.log
    under an auto-restarting service tells the admin nothing."""
    return SystemExit(f"[config] {message}\n"
                      f"[config] Edit {os.path.join(os.path.dirname(__file__), '.env_setup')} "
                      f"(or re-run install.ps1) and restart the service.")


def _require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise _config_error(f"{name} is not set.")
    return value


def _require_id(name: str) -> int:
    value = _require_env(name)
    if not value.isdigit():
        raise _config_error(f"{name} must be a numeric Discord ID, got {value!r}.")
    return int(value)


def _require_ids(name: str) -> list[int]:
    raw = _require_env(name)
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise _config_error(
                f"{name} must be numeric Discord IDs separated by commas, got {part!r}.")
        ids.append(int(part))
    if not ids:
        raise _config_error(f"{name} contains no Discord IDs.")
    return ids


BOT_TOKEN = _require_env("BOT_TOKEN")
AUTHORIZED_USER_IDS = _require_ids("AUTHORIZED_USER_IDS")
GUILD_ID = _require_id("GUILD_ID")
SERVICE_NAME = os.getenv("SERVICE_NAME", "Arma3Server")
HC_SERVICE_NAME = os.getenv("HC_SERVICE_NAME", "Arma3HeadlessClient")
QUERY_HOST = os.getenv("QUERY_HOST", "127.0.0.1")
QUERY_PORT = int(os.getenv("QUERY_PORT", "2303"))
# Steam username with a cached SteamCMD login on this machine.
# Required for /download (automated downloads); leave empty to use manual scripts.
STEAM_USERNAME = os.getenv("STEAM_USERNAME", "")
# Server passwords come from .env_setup (set by install.ps1) — never via Discord.
SERVER_PASSWORD = os.getenv("SERVER_PASSWORD", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
# Placeholders from .env_setup.example: writing one of these into server.cfg
# would hand #login admin rights to anyone who can read this repo.
PLACEHOLDER_ADMIN_PASSWORDS = {"changeme", "change_me", "password", "admin",
                               "your_admin_password_here"}

# Project root = two levels up from this file (…\A3SAT2\scripts\python\A3Setup.py)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# nssm.exe: next to the project first, then the A3SAT install
NSSM = os.path.join(PROJECT_ROOT, "nssm.exe")
if not os.path.isfile(NSSM):
    NSSM = "C:\\steamgames\\a3server\\A3SAT\\nssm.exe"

# Server/steamcmd locations are configurable via .env_setup, with the VM defaults
SERVER_BASE = os.getenv("SERVER_BASE", "C:\\steamgames\\a3server")
KEYS_DIR = os.path.join(SERVER_BASE, "keys")
WORKSHOP_BASE = os.getenv("WORKSHOP_BASE", "C:\\steamcmd\\steamapps\\workshop\\content\\107410")
STEAMCMD = os.getenv("STEAMCMD", "C:\\steamcmd\\steamcmd.exe")
PROFILES_STORE = os.path.join(PROJECT_ROOT, "profiles_store")
SAVES_DIR = os.path.join(SERVER_BASE, "profiles", "Users", "server")
SERVER_CFG_PATH = os.path.join(SERVER_BASE, "server.cfg")

# Known mission templates per mission mod WORKSHOP ID (folder names vary with
# the preset's display name, ids don't). Extended by the dynamic mpmissions scan.
KNOWN_MISSION_TEMPLATES = {
    "2867537125": {  # Antistasi Community
        "Altis": "Antistasi_Altis.Altis",
        "Stratis": "Antistasi_Stratis.Stratis",
        "Tanoa": "Antistasi_Tanoa.Tanoa",
        "Malden": "Antistasi_Malden.Malden",
        "Livonia": "Antistasi_Enoch.Enoch",
    },
    # "3020755032": Antistasi Ultimate — add template names once confirmed
}

DEFAULT_SERVER_CFG = """\
hostname = "{hostname}";
password = "{password}";
passwordAdmin = "{passwordAdmin}";
maxPlayers = {maxPlayers};
persistent = 1;
timeStampFormat = "short";
BattlEye = 1;

motd[] = {{"Welcome"}};
motdInterval = 9999;

voteMissionPlayers = 1;
voteThreshold = 0.33;

allowedFilePatching = 2;
steamValidationMaxTime = 10;

// Large mod sets overflow the default 1024-byte Steam query buffer, which
// makes clients receive an incomplete mod/signature list ("mission corrupt",
// no role selection). Raise it for mod-heavy servers.
steamProtocolMaxDataSize = 8192;

headlessClients[] = {{"127.0.0.1"}};
localClient[] = {{"127.0.0.1"}};

class Missions
{{
    class Mission1
    {{
        template = "{template}";
        difficulty = "Regular";
        class Params
        {{
            // 0 = NEW GAME, 1 = LOAD GAME. A fresh setup has no saves yet, so
            // starting in LOAD GAME leaves the mission with nothing to load.
            gameMode = 0;
            autoLoadLastGame = 60;
            autoSaveInterval = 1800;
            A3A_GCThreshold = 3600;
            A3A_builderPermissions = 1;
        }};
    }};
}};
"""


# ---------------------------------------------------------------- helpers

def write_text_atomic(path: str, content: str, encoding: str = "utf-8"):
    """Write a config/state file without ever leaving a truncated one behind.

    Opening the target directly in "w" truncates it before the first byte is
    written, so an encoding error or a crash mid-write destroys the previous
    contents — fatal for server.cfg and last_setup.json. Encode first, then
    replace the file in one step."""
    data = content.encode(encoding)
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def sanitize_cfg_value(value: str) -> str:
    """Strip characters that could break out of a server.cfg string literal.
    Prevents config injection via Discord-supplied values (e.g. server name)."""
    return re.sub(r'["\';{}\r\n\\]', "", value).strip()


def is_arma_preset_html(filename: str, content: bytes) -> tuple[bool, str]:
    """Validate an uploaded file as an Arma 3 launcher preset export."""
    if not filename.lower().endswith(".html"):
        return False, "File must be a `.html` launcher preset export."
    if len(content) > 2 * 1024 * 1024:
        return False, "File is too large for a launcher preset (max 2 MB)."
    head = content[:4096].decode("utf-8", errors="ignore")
    # The launcher emits arma:Type "preset" (preset export) or "list" (mod list
    # export); both have the same ModContainer structure.
    if not re.search(r'name="arma:Type"\s+content="(preset|list)"', head):
        return False, ("Not an Arma 3 launcher export - create it via "
                       "MODS -> PRESET -> SHARE -> Export to file.")
    return True, ""


def sanitize_param_value(value: str) -> str:
    """Strip whitespace and shell-breaking characters from a value embedded in
    an NSSM launch parameter string (e.g. the HC's -password=...)."""
    return re.sub(r'[\s"\';{}\\]', "", value)


def workshop_ready(mod_id: str) -> bool:
    """A mod counts as downloaded only if its workshop folder exists AND is
    non-empty — an empty folder is the residue of an interrupted download."""
    path = os.path.join(WORKSHOP_BASE, mod_id)
    return os.path.isdir(path) and any(os.scandir(path))


def sanitize_mod_name(display_name: str) -> str:
    """Turn a preset display name into a safe @Folder name."""
    name = re.sub(r"[^A-Za-z0-9]+", "", display_name)
    return f"@{name[:40]}" if name else "@UnknownMod"


def parse_preset_html(html: str) -> dict[str, str]:
    """Parse an Arma 3 launcher preset HTML. Returns {workshop_id: display_name}."""
    mods = {}
    rows = re.findall(
        r'data-type="DisplayName">([^<]+)</td>.*?\?id=(\d+)',
        html, re.DOTALL
    )
    for display_name, mod_id in rows:
        # Unescape the few entities the launcher emits
        clean = display_name.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()
        mods[mod_id] = clean
    return mods


def build_folder_map(mods: dict[str, str]) -> dict[str, str]:
    """Map workshop ids to unique @Folder names. On sanitized-name collision the
    workshop id is appended so two different mods never share a folder."""
    folder_map = {}
    used = set()
    for mod_id, display_name in mods.items():
        folder = sanitize_mod_name(display_name)
        if folder.lower() in used:
            folder = f"{folder}_{mod_id}"
        used.add(folder.lower())
        folder_map[mod_id] = folder
    return folder_map


def get_nssm_params(service: str) -> str:
    """Read a service's AppParameters. nssm may print UTF-16LE or plain text
    depending on version/host, and a wrong decode yields plausible-looking
    garbage — so validate the result actually looks like launch parameters."""
    result = subprocess.run([NSSM, "get", service, "AppParameters"],
                            capture_output=True)
    raw = result.stdout
    fallback = ""
    for encoding in ("utf-16-le", "utf-8", "mbcs", "latin-1"):
        try:
            cand = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        cand = cand.replace("\x00", "").replace("﻿", "").strip()
        if not cand:
            continue
        # Arma launch params always contain at least one of these
        if any(tok in cand for tok in ("-port=", "-mod=", "-config=", "-profiles=", "-client")):
            return cand
        # Otherwise keep the first result that is at least plain ASCII
        if not fallback and cand.isprintable():
            fallback = cand
    return fallback


def set_nssm_params(service: str, params: str) -> bool:
    result = subprocess.run([NSSM, "set", service, "AppParameters", params],
                            capture_output=True, text=True)
    return result.returncode == 0


_TEMPLATE_RE = re.compile(rb"Antistasi[A-Za-z0-9_]*\.[A-Za-z0-9_]+")
_NON_MISSION_SUFFIXES = (".paa", ".png", ".jpg", ".sqf", ".hpp", ".cpp", ".com",
                         ".net", ".org", ".pbo", ".xml", ".bin")


def extract_templates_from_mod(mod_dir: str) -> set[str]:
    """Antistasi 3.x/Ultimate register their missions inside the mod's addons
    rather than shipping mpmissions files, so read the template names straight
    out of the .pbo binaries."""
    found = set()
    for root, _, files in os.walk(mod_dir):
        for fname in files:
            if not fname.lower().endswith(".pbo"):
                continue
            try:
                with open(os.path.join(root, fname), "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            for m in _TEMPLATE_RE.findall(data):
                name = m.decode("ascii", errors="ignore")
                if name.lower().endswith(_NON_MISSION_SUFFIXES):
                    continue
                # A template is "MissionName.MapName" — map part must be non-empty
                mission, _, world = name.partition(".")
                if world and world.lower() == name.split(".")[-1].lower():
                    found.add(name)
    return found


# Worlds shipped by the base game / official DLC — always present
VANILLA_WORLDS = {"altis", "stratis", "malden", "tanoa", "enoch", "vr",
                  "cam_lao_nam", "spe_normandy", "spe_mortain", "sefrouramal",
                  "gm_weferlingen_summer", "gm_weferlingen_winter"}

# Keyed by (world, mod set): a "not found" verdict is only valid for the exact
# set of folders that was scanned, or adding the missing terrain mod later would
# keep returning the stale False.
_world_cache: dict[tuple[str, tuple[str, ...]], bool] = {}


def world_available(world: str, mod_folders: list[str]) -> bool | None:
    """Is the terrain for `world` provided by any of these installed mods?

    Terrain pbos contain 'worlds\\<name>\\<name>.wrp', and a pbo's file list
    sits in its header, so reading the first chunk of each pbo is enough.
    Returns None when we can't tell (nothing scannable on disk)."""
    world_key = world.lower()
    if world_key in VANILLA_WORLDS:
        return True
    key = (world_key, tuple(sorted(f.lower() for f in mod_folders)))
    if key in _world_cache:
        return _world_cache[key]
    needle = f"{world_key}.wrp".encode()
    scanned_any = False
    for folder in mod_folders:
        mod_dir = os.path.join(SERVER_BASE, folder)
        if not os.path.isdir(mod_dir):
            continue
        for root, _, files in os.walk(mod_dir):
            for fname in files:
                if not fname.lower().endswith(".pbo"):
                    continue
                scanned_any = True
                try:
                    with open(os.path.join(root, fname), "rb") as fh:
                        head = fh.read(512 * 1024)  # pbo header holds the file list
                except OSError:
                    continue
                if needle in head.lower():
                    _world_cache[key] = True
                    return True
    if not scanned_any:
        return None
    _world_cache[key] = False
    return False


def scan_available_maps(mods=None) -> dict[str, str]:
    """Return {label: template} for every mission template we can offer.

    Templates are read from the mission mods of THIS setup (workshop cache or
    installed folder), so unrelated mods in the cache never leak in. Falls back
    to the last applied setup, the known-template table, and mpmissions."""
    last = load_last_setup()  # {mod_id: @Folder} of the applied setup
    if isinstance(mods, dict):
        id_to_name = mods                       # pending setup: {id: display name}
    else:
        ids = set(mods) if mods else set(last)  # ids only, or the applied setup
        id_to_name = {mid: last.get(mid, "") for mid in ids}

    available = {}
    for mid, display_name in id_to_name.items():
        # Only this setup's mission mods are scanned — unrelated mods sitting in
        # the workshop cache are never looked at. The name check keeps us from
        # walking tens of GB of ordinary addons.
        if "antistasi" not in f"{display_name} {last.get(mid, '')}".lower() \
                and mid not in KNOWN_MISSION_TEMPLATES:
            continue
        installed = os.path.join(SERVER_BASE, last[mid]) if last.get(mid) else None
        scanned = False
        for candidate in (os.path.join(WORKSHOP_BASE, mid), installed):
            if candidate and os.path.isdir(candidate):
                for template in extract_templates_from_mod(candidate):
                    # Label = the world part, which is what users think of
                    available[template.split(".", 1)[1]] = template
                scanned = True
                break
        # Fallback table for mods that aren't on disk yet
        if not scanned and mid in KNOWN_MISSION_TEMPLATES:
            available.update(KNOWN_MISSION_TEMPLATES[mid])

    mpmissions = os.path.join(SERVER_BASE, "mpmissions")
    if os.path.isdir(mpmissions):
        for f in os.listdir(mpmissions):
            if f.lower().endswith(".pbo"):
                template = f[:-4]  # strip .pbo -> "MissionName.MapName"
                available[template] = template
    return dict(sorted(available.items()))


def collect_saves_size() -> int:
    total = 0
    if os.path.isdir(SAVES_DIR):
        for root, _, files in os.walk(SAVES_DIR):
            for f in files:
                total += os.path.getsize(os.path.join(root, f))
    return total


def make_backup(name: str) -> str:
    """Snapshot current NSSM params, server.cfg and saves into a profile folder."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safe_name = re.sub(r"[^A-Za-z0-9_\-]+", "_", name) or "backup"
    folder = os.path.join(PROFILES_STORE, f"{stamp}_{safe_name}")
    os.makedirs(folder, exist_ok=True)

    profile = {
        "name": safe_name,
        "created": stamp,
        "server_params": get_nssm_params(SERVICE_NAME),
        "hc_params": get_nssm_params(HC_SERVICE_NAME),
    }
    with open(os.path.join(folder, "profile.json"), "w") as f:
        json.dump(profile, f, indent=2)

    if os.path.isfile(SERVER_CFG_PATH):
        shutil.copy2(SERVER_CFG_PATH, os.path.join(folder, "server.cfg"))

    if os.path.isdir(SAVES_DIR):
        shutil.copytree(SAVES_DIR, os.path.join(folder, "saves"), dirs_exist_ok=True)

    return folder


def list_profiles() -> list[dict]:
    profiles = []
    if not os.path.isdir(PROFILES_STORE):
        return profiles
    for entry in sorted(os.listdir(PROFILES_STORE)):
        pj = os.path.join(PROFILES_STORE, entry, "profile.json")
        if os.path.isfile(pj):
            try:
                with open(pj) as f:
                    data = json.load(f)
                data["folder"] = entry
                profiles.append(data)
            except Exception:
                continue
    return profiles


def restore_profile(folder_name: str) -> tuple[bool, str]:
    # Only accept names that actually exist in the store — blocks path
    # traversal like "..\..\x" reaching outside PROFILES_STORE.
    valid = {p["folder"] for p in list_profiles()}
    if folder_name not in valid:
        return False, f"Profile `{folder_name}` not found."
    folder = os.path.join(PROFILES_STORE, folder_name)
    pj = os.path.join(folder, "profile.json")
    if not os.path.isfile(pj):
        return False, f"Profile `{folder_name}` not found."

    with open(pj) as f:
        profile = json.load(f)

    # Params captured by an older buggy version are unusable — restore the rest
    # (config + saves) rather than refusing, and say the mod list was skipped.
    sp, hp = profile.get("server_params", ""), profile.get("hc_params", "")
    params_ok = bool(sp) and ("-mod=" in sp or "-config=" in sp)

    changes = []
    if params_ok:
        set_nssm_params(SERVICE_NAME, sp)
        changes.append("server NSSM params")
        if hp:
            set_nssm_params(HC_SERVICE_NAME, hp)
            changes.append("HC NSSM params")

    cfg_backup = os.path.join(folder, "server.cfg")
    if os.path.isfile(cfg_backup):
        shutil.copy2(cfg_backup, SERVER_CFG_PATH)
        changes.append("server.cfg")

    saves_backup = os.path.join(folder, "saves")
    if os.path.isdir(saves_backup):
        # Replace, don't merge: merging left every save file the snapshot does
        # not contain in place, so a newer campaign save could survive a
        # rollback and be loaded again by autoLoadLastGame. The caller has
        # already taken an auto_pre_restore snapshot of what is being replaced.
        try:
            if os.path.isdir(SAVES_DIR):
                shutil.rmtree(SAVES_DIR)
            shutil.copytree(saves_backup, SAVES_DIR)
            changes.append("saves")
        except OSError as e:
            changes.append(f"⚠️ saves NOT restored ({e.__class__.__name__} — is the server "
                           f"still running?); the profile's other parts were applied")

    # Keys must match the restored mod set, not the previous one
    folders = mod_folders_from_params(sp) if params_ok else []
    if folders:
        rebuild_keys(folders)
        changes.append("keys rebuilt")

    if not params_ok:
        changes.append("⚠️ stored mod list unusable (old backup) — the mod "
                       "parameters were left unchanged; set them manually if this "
                       "profile used a different mod set")
    return True, ", ".join(changes)


def install_mods_from_workshop(mods: dict[str, str]) -> tuple[list[str], list[str], dict[str, str]]:
    """Copy downloaded workshop mods into the server folder.
    Returns (installed, missing, id_to_folder)."""
    installed, missing = [], []
    id_to_folder = build_folder_map(mods)
    os.makedirs(KEYS_DIR, exist_ok=True)
    for mod_id, folder_name in id_to_folder.items():
        src = os.path.join(WORKSHOP_BASE, mod_id)
        dest = os.path.join(SERVER_BASE, folder_name)
        if os.path.isdir(dest):
            installed.append(folder_name)  # already present — never delete/overwrite
            continue
        if not workshop_ready(mod_id):
            missing.append(f"{folder_name} ({mod_id})")
            continue
        shutil.copytree(src, dest)
        installed.append(folder_name)
        for root, _, files in os.walk(dest):
            for f in files:
                if f.lower().endswith(".bikey"):
                    shutil.copy2(os.path.join(root, f), KEYS_DIR)
    return installed, missing, id_to_folder


_mod_details_cache: dict = {"key": None, "at": 0.0, "data": {}}


def fetch_mod_details(mod_ids: list[str]) -> dict[str, dict]:
    """Query the Steam web API for workshop item details.
    Returns {mod_id: {"size": int, "time_updated": int, "title": str}};
    items the API doesn't report are omitted. Results are cached for 5 minutes
    to avoid hammering the API from consecutive commands."""
    cache_key = tuple(sorted(mod_ids))
    now = datetime.now().timestamp()
    if _mod_details_cache["key"] == cache_key and now - _mod_details_cache["at"] < 300:
        return _mod_details_cache["data"]
    url = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
    data = {"itemcount": len(mod_ids)}
    for i, mid in enumerate(mod_ids):
        data[f"publishedfileids[{i}]"] = mid
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"User-Agent": "Mozilla/5.0"})
    details_map = {}
    with urllib.request.urlopen(req, timeout=15) as r:
        details = json.load(r).get("response", {}).get("publishedfiledetails", [])
    for item in details:
        try:
            details_map[str(item["publishedfileid"])] = {
                "size": int(item.get("file_size", 0)),
                "time_updated": int(item.get("time_updated", 0)),
                "title": item.get("title", ""),
            }
        except (KeyError, ValueError, TypeError):
            continue
    _mod_details_cache.update(key=cache_key, at=now, data=details_map)
    return details_map


def fetch_mod_sizes(mod_ids: list[str]) -> dict[str, int]:
    """Convenience wrapper: {mod_id: size} for items reporting a positive size."""
    return {mid: d["size"] for mid, d in fetch_mod_details(mod_ids).items()
            if d["size"] > 0}


def check_disk_space(mods: dict[str, str]) -> dict:
    """Estimate disk needs per destination drive.

    Two things consume space:
      - the SteamCMD download cache under WORKSHOP_BASE (mods not yet downloaded)
      - the server @Folder copies under SERVER_BASE (mods not yet installed)
    These may be on different drives, so each is checked against its own drive.
    Returns a dict with per-destination numbers and a combined verdict.
    """
    sizes = fetch_mod_sizes(list(mods.keys()))
    folder_map = build_folder_map(mods)
    download_bytes = 0   # will land under WORKSHOP_BASE
    install_bytes = 0    # will land under SERVER_BASE
    unknown = []
    for mid in mods:
        size = sizes.get(mid)
        downloaded = os.path.isdir(os.path.join(WORKSHOP_BASE, mid))
        installed = os.path.isdir(os.path.join(SERVER_BASE, folder_map[mid]))
        if size is None:
            if not (downloaded and installed):
                unknown.append(mid)
            continue
        if not downloaded:
            download_bytes += size
        if not installed:
            install_bytes += size

    workshop_drive = os.path.splitdrive(os.path.abspath(WORKSHOP_BASE))[0].upper()
    server_drive = os.path.splitdrive(os.path.abspath(SERVER_BASE))[0].upper()
    same_drive = workshop_drive == server_drive

    workshop_free = shutil.disk_usage(os.path.splitdrive(os.path.abspath(WORKSHOP_BASE))[0] + "\\").free
    server_free = shutil.disk_usage(os.path.splitdrive(os.path.abspath(SERVER_BASE))[0] + "\\").free

    if same_drive:
        ok = workshop_free > (download_bytes + install_bytes) * 1.1
    else:
        ok = (workshop_free > download_bytes * 1.1) and (server_free > install_bytes * 1.1)

    return {
        "download_bytes": download_bytes,
        "install_bytes": install_bytes,
        "workshop_drive": workshop_drive,
        "server_drive": server_drive,
        "same_drive": same_drive,
        "workshop_free": workshop_free,
        "server_free": server_free,
        "ok": ok,
        "unknown": unknown,
    }


def format_map_list(maps: dict[str, str], budget: int = 1500) -> str:
    """Compact, comma-separated map list for a Discord message, truncated to
    stay inside the 2000-char limit."""
    labels = list(maps)
    out, used, shown = [], 0, 0
    for label in labels:
        if used + len(label) + 2 > budget:
            break
        out.append(label)
        used += len(label) + 2
        shown += 1
    rest = len(labels) - shown
    text = "```\n" + ", ".join(out) + ("\n...and %d more" % rest if rest else "") + "\n```"
    return text


def fmt_gb(n: int) -> str:
    return f"{n / 1024**3:.1f} GB"


LAST_SETUP_FILE = os.path.join(PROJECT_ROOT, "last_setup.json")


def save_last_setup(id_to_folder: dict[str, str], names: dict[str, str] | None = None):
    payload = {"applied_at": int(datetime.now().timestamp()), "mods": id_to_folder}
    # Display names let us regenerate a launcher preset later (/preset)
    payload["names"] = names if names is not None else load_last_names()
    # Written atomically: this file is saved right before a restart, and a
    # half-written one used to break every command that reads it.
    write_text_atomic(LAST_SETUP_FILE, json.dumps(payload, indent=2))


def _read_last_setup() -> dict:
    """The applied-setup state, or {} when it is missing or unreadable.

    A truncated last_setup.json must not take down /update, /preset,
    /checkupdates, /cleanup_workshop and /setup_map all at once."""
    try:
        with open(LAST_SETUP_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_last_names() -> dict[str, str]:
    """Workshop display names of the last applied setup (may be empty for
    setups applied before this was recorded)."""
    names = _read_last_setup().get("names", {})
    return names if isinstance(names, dict) else {}


PRESET_HTML = """<?xml version="1.0" encoding="utf-8"?>
<html>
  <!--Created by A3Bot from the applied server setup-->
  <head>
    <meta name="arma:Type" content="preset" />
    <meta name="arma:PresetName" content="{preset_name}" />
    <meta name="generator" content="Arma 3 Launcher - https://arma3.com" />
    <title>Arma 3</title>
  </head>
  <body>
    <h1>Arma 3  - Preset <strong>{preset_name}</strong></h1>
    <p class="before-list">
      <em>To import this preset, drag this file onto the Launcher window. Or click the MODS tab, then PRESET in the top right, then IMPORT at the bottom, and finally select this file.</em>
    </p>
    <div class="mod-list">
      <table>
{rows}      </table>
    </div>
    <div class="footer">
      <span>Created by Arma 3 Launcher by Bohemia Interactive.</span>
    </div>
  </body>
</html>
"""

PRESET_ROW = """        <tr data-type="ModContainer">
          <td data-type="DisplayName">{name}</td>
          <td>
            <span class="from-steam">Steam</span>
          </td>
          <td>
            <a href="https://steamcommunity.com/sharedfiles/filedetails/?id={mid}" data-type="Link">https://steamcommunity.com/sharedfiles/filedetails/?id={mid}</a>
          </td>
        </tr>
"""


def build_preset_html(preset_name: str = "Server Mods") -> tuple[str, int]:
    """Rebuild an importable launcher preset from the applied setup.
    Only mods actually in the server's -mod= line are included."""
    mods = load_last_setup()
    names = load_last_names()
    loaded_folders = set(mod_folders_from_params(get_nssm_params(SERVICE_NAME)))
    rows = []
    for mid, folder in sorted(mods.items(), key=lambda kv: names.get(kv[0], kv[1]).lower()):
        if loaded_folders and folder not in loaded_folders:
            continue  # in the setup but not actually loaded by the server
        display = names.get(mid) or folder.lstrip("@")
        rows.append(PRESET_ROW.format(
            name=display.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"),
            mid=mid))
    return PRESET_HTML.format(preset_name=preset_name, rows="".join(rows)), len(rows)


def load_last_setup() -> dict[str, str]:
    """Returns {mod_id: folder} of the last applied setup."""
    data = _read_last_setup()
    # Backward compat: old format was a flat {id: folder} dict
    mods = data.get("mods", data)
    return mods if isinstance(mods, dict) else {}


def load_applied_at() -> int:
    """Unix timestamp of the last /setup_apply or /update (0 if unknown)."""
    try:
        return int(_read_last_setup().get("applied_at", 0))
    except (TypeError, ValueError):
        return 0


def find_outdated_mods() -> list[tuple[str, str, str]]:
    """Compare Steam's time_updated against the last apply time.
    Returns [(mod_id, folder, title), ...] for mods updated since then."""
    mods = load_last_setup()
    applied_at = load_applied_at()
    if not mods or not applied_at:
        return []
    details = fetch_mod_details(list(mods.keys()))
    outdated = []
    for mid, folder in mods.items():
        d = details.get(mid)
        if d and d["time_updated"] > applied_at:
            outdated.append((mid, folder, d["title"] or folder))
    return outdated


def update_installed_mods(outdated_ids: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Re-copy updated mods from the workshop cache over their server @Folders.
    Only replaces a folder with a newer copy of the same mod.
    Returns (updated, needs_download, failed)."""
    mods = load_last_setup()
    updated, needs_download, failed = [], [], []
    for mid in outdated_ids:
        folder = mods.get(mid)
        if not folder:
            continue
        src = os.path.join(WORKSHOP_BASE, mid)
        dest = os.path.join(SERVER_BASE, folder)
        if not workshop_ready(mid):
            needs_download.append(f"{folder} ({mid})")
            continue
        # Move the old copy aside instead of deleting it in place: if the server
        # is still holding a .pbo open, the move fails before anything is lost,
        # where an in-place rmtree would leave a half-deleted mod the server
        # can no longer load.
        stale = f"{dest}.old"
        try:
            if os.path.isdir(stale):
                shutil.rmtree(stale, ignore_errors=True)
            if os.path.isdir(dest):
                os.rename(dest, stale)
        except OSError as e:
            failed.append(f"{folder} ({e.__class__.__name__}: in use by the running server?)")
            continue
        try:
            shutil.copytree(src, dest)
        except OSError as e:
            # Put the working copy back rather than leaving the mod missing
            if os.path.isdir(stale) and not os.path.isdir(dest):
                os.rename(stale, dest)
            failed.append(f"{folder} ({e.__class__.__name__})")
            continue
        shutil.rmtree(stale, ignore_errors=True)
        for root, _, files in os.walk(dest):
            for f in files:
                if f.lower().endswith(".bikey"):
                    shutil.copy2(os.path.join(root, f), KEYS_DIR)
        updated.append(folder)
    # Only refresh applied_at when nothing was skipped — otherwise skipped mods
    # would be considered up to date and never flagged again.
    if updated and not needs_download and not failed:
        save_last_setup(mods)
    return updated, needs_download, failed


def cleanup_workshop() -> tuple[int, int, list[str]]:
    """Delete workshop download folders for mods from the last applied setup —
    but only if the corresponding @Folder exists in the server base.
    Returns (folders_removed, bytes_freed, skipped)."""
    removed = 0
    freed = 0
    skipped = []
    for mid, folder in load_last_setup().items():
        src = os.path.join(WORKSHOP_BASE, mid)
        if not os.path.isdir(src):
            continue
        if not os.path.isdir(os.path.join(SERVER_BASE, folder)):
            skipped.append(f"{folder} ({mid})")
            continue
        size = sum(os.path.getsize(os.path.join(root, f))
                   for root, _, files in os.walk(src) for f in files)
        shutil.rmtree(src)
        removed += 1
        freed += size
    return removed, freed, skipped


def generate_steamcmd_script(mod_ids: list[str]) -> str:
    # No force_install_dir: workshop items then download into SteamCMD's own
    # steamapps\workshop\content\107410 — which is exactly WORKSHOP_BASE.
    # (With force_install_dir they'd land under that dir instead and the bot
    # would never see them.)
    user = STEAM_USERNAME or "YOUR_STEAM_USERNAME"
    lines = [f"login {user}"]
    lines += [f"workshop_download_item 107410 {mid}" for mid in mod_ids]
    lines.append("quit")
    return "\n".join(lines)


def missing_from_workshop(mod_ids: list[str]) -> list[str]:
    return [mid for mid in mod_ids if not workshop_ready(mid)]


async def run_steamcmd_download(mod_ids: list[str], followup: discord.Webhook,
                                max_attempts: int = 5) -> list[str]:
    """Run SteamCMD to download the given workshop items, retrying because big
    mods routinely time out. Returns the list of ids still missing afterwards.
    Requires STEAM_USERNAME with a cached login on this machine."""
    script_path = os.path.join(PROJECT_ROOT, "download_mods.txt")
    with open(script_path, "w") as f:
        f.write(generate_steamcmd_script(mod_ids))

    remaining = missing_from_workshop(mod_ids)
    for attempt in range(1, max_attempts + 1):
        if not remaining:
            break
        started = datetime.now()
        await followup.send(
            f"⬇️ SteamCMD attempt {attempt}/{max_attempts} — "
            f"{len(remaining)} mods to download...")
        proc = await asyncio.create_subprocess_exec(
            STEAMCMD, "+runscript", script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        try:
            out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=3600)
        except asyncio.TimeoutError:
            proc.kill()
            await followup.send("⚠️ SteamCMD attempt timed out after 60 minutes — retrying.")
            out_bytes = b""
        output = out_bytes.decode(errors="ignore")
        # Keep the full output for diagnostics
        try:
            log_dir = os.path.join(PROJECT_ROOT, "logs")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "steamcmd.log"), "a", encoding="utf-8") as lf:
                lf.write(f"\n===== attempt {attempt} @ {datetime.now().isoformat()} =====\n{output}\n")
        except OSError:
            pass
        # Abort early on auth problems instead of burning all retries
        if any(s in output for s in ("Login Failure", "Invalid Password",
                                     "FAILED (Invalid Password)", "Two-factor code mismatch",
                                     "Account Logon Denied")):
            await followup.send(
                "❌ SteamCMD login failed — the cached session has expired or the "
                "username is wrong. Log in once interactively on the machine:\n"
                f"```powershell\n& \"{STEAMCMD}\" +login {STEAM_USERNAME or 'YOUR_STEAM_USERNAME'} +quit\n```")
            return remaining

        before = set(remaining)
        remaining = missing_from_workshop(mod_ids)
        got = len(before) - len(remaining)
        elapsed = int((datetime.now() - started).total_seconds())

        # Surface SteamCMD's own error reasons instead of retrying silently
        errors = re.findall(r"ERROR! Download item (\d+) failed \(([^)]+)\)", output)
        if errors and got == 0:
            reasons = "\n".join(f"- `{mid}`: {reason}" for mid, reason in errors[:10])
            await followup.send(
                f"⚠️ Attempt {attempt} downloaded nothing in {elapsed}s. SteamCMD reported:\n{reasons}\n"
                + ("_'File Not Found' usually means the item was removed from the Workshop "
                   "or is private — it cannot be downloaded._"
                   if any("Not Found" in r for _, r in errors) else ""))
            # Permanent failures won't fix themselves — stop retrying
            if all(("Not Found" in r or "Access Denied" in r) for _, r in errors):
                return remaining
        else:
            await followup.send(
                f"✔️ Attempt {attempt}: {got} mods done in {elapsed}s, {len(remaining)} left.")
    return remaining


def rebuild_keys(mod_folders: list[str]):
    """Rebuild keys\\ to contain exactly the .bikey files of the given mods
    (plus Arma's own a3.bikey). Prevents stale keys from previously loaded
    mods letting clients join with mods the server no longer runs."""
    os.makedirs(KEYS_DIR, exist_ok=True)
    # A key the running server holds open must not abort the whole rebuild —
    # a stale key left behind is far less harmful than a half-rebuilt keys\.
    for f in os.listdir(KEYS_DIR):
        if f.lower().endswith(".bikey") and not f.lower().startswith("a3"):
            try:
                os.remove(os.path.join(KEYS_DIR, f))
            except OSError as e:
                print(f"[keys] Could not remove {f}: {e}", flush=True)
    for folder in mod_folders:
        mod_dir = os.path.join(SERVER_BASE, folder)
        if not os.path.isdir(mod_dir):
            continue
        for root, _, files in os.walk(mod_dir):
            for f in files:
                if f.lower().endswith(".bikey"):
                    try:
                        shutil.copy2(os.path.join(root, f), KEYS_DIR)
                    except OSError as e:
                        print(f"[keys] Could not copy {f}: {e}", flush=True)


def mod_folders_from_params(params: str) -> list[str]:
    """Extract the @Folder list from a -mod=... launch parameter string."""
    m = re.search(r"-mod=([^\s]+)", params)
    if not m:
        return []
    return [p for p in m.group(1).split(";") if p]


# ------------------------------------------------- service management helpers

def get_service_state(service_name: str) -> str:
    try:
        result = subprocess.run(["sc", "query", service_name],
                                capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError:
        return ""


def get_server_start_time() -> datetime | None:
    # Server and HC share the same exe name — filter out the HC via its
    # -client argument so uptime reports the actual server process.
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='arma3server_x64.exe'\" | "
             "Where-Object { $_.CommandLine -notlike '*-client*' } | "
             "Sort-Object CreationDate | Select-Object -First 1).CreationDate.ToString('o')"],
            capture_output=True, text=True, timeout=10
        )
        raw = result.stdout.strip()
        if not raw:
            return None
        return datetime.fromisoformat(raw)
    except Exception:
        return None


# Async wrappers — sc/nssm/a2s calls block for seconds; keep them off the event
# loop so the bot stays responsive while polling or controlling services.

async def a_service_state(service_name: str) -> str:
    return await asyncio.to_thread(get_service_state, service_name)


async def a_sc(*args, check: bool = False):
    return await asyncio.to_thread(
        subprocess.run, ["sc", *args], capture_output=True, check=check)


async def a_query_server(timeout: float = 3.0):
    return await asyncio.to_thread(a2s.info, (QUERY_HOST, QUERY_PORT), timeout)


async def restart_headless_client(delay: int = 90):
    await asyncio.sleep(delay)
    try:
        await a_sc("stop", HC_SERVICE_NAME)
        await asyncio.sleep(5)
        await a_sc("start", HC_SERVICE_NAME, check=True)
        print(f"[HC] Headless client restarted after {delay}s delay.", flush=True)
    except Exception as e:
        print(f"[HC] Failed to restart headless client: {e}", flush=True)


async def wait_until_joinable(followup: discord.Webhook, timeout: int = 300):
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        try:
            await a_query_server()
            await followup.send("✅ Server is now **joinable**!")
            return
        except Exception:
            continue
    await followup.send("⚠️ Server did not become joinable within 5 minutes.")


async def stop_server_and_wait(timeout: int = 120) -> bool:
    """Stop the server service and wait until it has fully exited."""
    if "RUNNING" not in await a_service_state(SERVICE_NAME):
        return True
    await a_sc("stop", SERVICE_NAME)
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        if "STOPPED" in await a_service_state(SERVICE_NAME):
            return True
    return False


async def start_server(followup: discord.Webhook, verb: str = "started"):
    """Start the server service, then watch joinability + restart the HC."""
    try:
        await a_sc("start", SERVICE_NAME, check=True)
        await followup.send(f"🟡 Service '{SERVICE_NAME}' {verb} — waiting for server to become joinable...")
        asyncio.create_task(wait_until_joinable(followup))
        asyncio.create_task(restart_headless_client(delay=90))
    except Exception as e:
        await followup.send(f"Failed to start service: {e}")


async def do_restart(followup: discord.Webhook) -> bool:
    """Stop-wait-start the server service, then watch joinability + HC.

    Starting while the old process is still shutting down just fails on the
    port, so a stop that never completed is reported rather than papered over
    with a "restarted" message."""
    if not await stop_server_and_wait():
        await followup.send(
            f"⚠️ Service '{SERVICE_NAME}' did not stop within 2 minutes — **not** starting it "
            f"again, since that would fail on the still-bound port. Check the service, then "
            f"use `/start`.")
        return False
    await start_server(followup, verb="restarted")
    return True


# ---------------------------------------------------------------- bot state

PENDING_FILE = os.path.join(PROJECT_ROOT, "pending_setups.json")


def _load_pending() -> dict[int, dict]:
    try:
        with open(PENDING_FILE) as f:
            return {int(k): v for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


def persist_pending():
    """Keep pending setups on disk so a bot/service restart doesn't lose the wizard state."""
    try:
        with open(PENDING_FILE, "w") as f:
            json.dump({str(k): v for k, v in pending_setups.items()}, f, indent=2)
    except OSError as e:
        print(f"[Pending] Failed to persist: {e}", flush=True)


pending_setups: dict[int, dict] = _load_pending()


class SetupClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print(f"A3Bot slash commands synced to guild {guild.id}.")


client = SetupClient()


def is_authorized(interaction: discord.Interaction) -> bool:
    return interaction.user.id in AUTHORIZED_USER_IDS


# ---------------------------------------------------------------- UI

class MapSelect(discord.ui.Select):
    def __init__(self, maps: dict[str, str]):
        options = [discord.SelectOption(label=label[:100], value=template[:100])
                   for label, template in list(maps.items())[:25]]
        super().__init__(placeholder="Choose a mission/map...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if not is_authorized(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        cfg = pending_setups.get(interaction.user.id)
        if not cfg or not cfg.get("mods"):
            # Stale dropdown from a setup that was already applied or abandoned
            await interaction.response.send_message(
                "This setup is no longer pending — start a new one with `/setup`.", ephemeral=True)
            return
        cfg["template"] = self.values[0]
        cfg["template_chosen"] = True
        persist_pending()
        await interaction.response.send_message(
            f"Mission template set to **{self.values[0]}**.\n"
            f"**Next:** run `/setup_apply restart:true` again to install, configure and go live.",
            ephemeral=True
        )


class MapSelectView(discord.ui.View):
    def __init__(self, maps: dict[str, str]):
        super().__init__(timeout=180)
        self.add_item(MapSelect(maps))


# ------------------------------------------------- service management commands

@client.tree.command(name="start", description="Starts the Arma 3 server service")
async def start(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("You are not authorized to execute this command.", ephemeral=True)
        return
    await interaction.response.defer()
    state = await a_service_state(SERVICE_NAME)
    if "RUNNING" in state:
        await interaction.followup.send(f"Service '{SERVICE_NAME}' is already running!")
        return
    try:
        await a_sc("start", SERVICE_NAME, check=True)
        await interaction.followup.send(f"🟡 Service '{SERVICE_NAME}' started — waiting for server to become joinable...")
        asyncio.create_task(wait_until_joinable(interaction.followup))
        asyncio.create_task(restart_headless_client(delay=90))
    except Exception as e:
        await interaction.followup.send(f"Failed to start service: {e}")


@client.tree.command(name="stop", description="Stops the Arma 3 server service")
async def stop(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("You are not authorized to execute this command.", ephemeral=True)
        return
    await interaction.response.defer()
    state = await a_service_state(SERVICE_NAME)
    if "STOPPED" in state or "STOP_PENDING" in state or "not running" in state.lower():
        await interaction.followup.send(f"Service '{SERVICE_NAME}' is already stopped!")
        return
    try:
        await a_sc("stop", SERVICE_NAME, check=True)
        await interaction.followup.send(f"Service '{SERVICE_NAME}' stopped successfully!")
    except Exception as e:
        await interaction.followup.send(f"Failed to stop service: {e}")


@client.tree.command(name="restart", description="Restarts the Arma 3 server service")
async def restart(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("You are not authorized to execute this command.", ephemeral=True)
        return
    await interaction.response.defer()
    await do_restart(interaction.followup)


@client.tree.command(name="status", description="Shows whether the server service is running or stopped")
async def status(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("You are not authorized to execute this command.", ephemeral=True)
        return
    await interaction.response.defer()
    state = await a_service_state(SERVICE_NAME)
    hc_state = await a_service_state(HC_SERVICE_NAME)

    if "RUNNING" in state:
        lines = [f"🟢 **{SERVICE_NAME}** is running."]
    elif "STOPPED" in state:
        lines = [f"🔴 **{SERVICE_NAME}** is stopped."]
    else:
        lines = [f"⚠️ **{SERVICE_NAME}** status unknown."]

    if "RUNNING" in hc_state:
        lines.append(f"🟢 **{HC_SERVICE_NAME}** is running.")
    elif "STOPPED" in hc_state:
        lines.append(f"🔴 **{HC_SERVICE_NAME}** is stopped.")
    elif not hc_state:
        lines.append(f"⚪ **{HC_SERVICE_NAME}** is not installed.")
    else:
        lines.append(f"⚠️ **{HC_SERVICE_NAME}** status unknown.")

    # Player count also tells us whether the HC actually connected
    if "RUNNING" in state:
        try:
            info = await a_query_server(timeout=5.0)
            lines.append(f"👥 Players: **{info.player_count}/{info.max_players}** "
                         f"_(the HC counts as one)_")
        except Exception:
            lines.append("⏳ No A2S response yet — mission still loading?")

    await interaction.followup.send("\n".join(lines))


@client.tree.command(name="uptime", description="Shows how long the server has been running")
async def uptime(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("You are not authorized to execute this command.", ephemeral=True)
        return
    await interaction.response.defer()
    state = await a_service_state(SERVICE_NAME)
    if "RUNNING" not in state:
        await interaction.followup.send(f"🔴 {SERVICE_NAME} is not running.")
        return
    start_time = await asyncio.to_thread(get_server_start_time)
    if not start_time:
        await interaction.followup.send("⚠️ Could not determine server start time.")
        return
    delta = datetime.now(start_time.tzinfo) - start_time
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    await interaction.followup.send(f"⏱️ {SERVICE_NAME} has been running for **{hours}h {minutes}m {seconds}s**.")


@client.tree.command(name="players", description="Shows the current player count on the server")
async def players(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("You are not authorized to execute this command.", ephemeral=True)
        return
    await interaction.response.defer()
    state = await a_service_state(SERVICE_NAME)
    if "RUNNING" not in state:
        await interaction.followup.send(f"🔴 {SERVICE_NAME} is not running.")
        return
    try:
        info = await a_query_server(timeout=5.0)
        await interaction.followup.send(f"👥 Players: **{info.player_count}/{info.max_players}**")
    except Exception:
        await interaction.followup.send("⚠️ Could not query server. Is a mission loaded?")


# ---------------------------------------------------------------- setup commands

@client.tree.command(name="setup", description="Start a server setup: attach your launcher preset")
@app_commands.describe(
    preset="The exported preset .html file from the Arma 3 launcher",
    name="Server name (default: Antistasi Server)",
    max_players="Max players (default: 20)",
    keep_unavailable="Keep mods Steam no longer offers (default: drop them)")
async def setup(interaction: discord.Interaction, preset: discord.Attachment,
                name: str = "Antistasi Server", max_players: int = 20,
                keep_unavailable: bool = False):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    content = await preset.read()
    ok, reason = is_arma_preset_html(preset.filename, content)
    if not ok:
        await interaction.followup.send(f"⚠️ {reason}")
        return
    raw = content.decode("utf-8", errors="ignore")
    mods = parse_preset_html(raw)
    if not mods:
        await interaction.followup.send("No mods found in that file — is it an Arma 3 launcher preset export?")
        return

    # Mods Steam reports nothing for are delisted/private — they can never be
    # downloaded, so drop them instead of failing later at /download.
    dropped_note = ""
    try:
        known = await asyncio.to_thread(fetch_mod_details, list(mods.keys()))
        unavailable = {mid: title for mid, title in mods.items()
                       if mid not in known and not workshop_ready(mid)}
        if unavailable:
            listed = "\n".join(f"- {t} (`{mid}`)" for mid, t in list(unavailable.items())[:10])
            more_u = f"\n...and {len(unavailable) - 10} more" if len(unavailable) > 10 else ""
            if keep_unavailable:
                dropped_note = (f"\n⚠️ **{len(unavailable)} mods are no longer on the Workshop** "
                                f"(kept — the download will fail):\n{listed}{more_u}\n")
            else:
                for mid in unavailable:
                    del mods[mid]
                dropped_note = (f"\n⚠️ **Skipped {len(unavailable)} mods that are no longer on the "
                                f"Workshop** (delisted or private):\n{listed}{more_u}\n"
                                f"_Use `keep_unavailable:true` to keep them anyway._\n")
    except Exception:
        pass

    pending_setups[interaction.user.id] = {
        "hostname": sanitize_cfg_value(name) or "Antistasi Server",
        "maxPlayers": str(max(1, min(127, max_players))),
        "template": "Antistasi_Altis.Altis",
        "template_chosen": False,
        "mods": mods,
    }
    persist_pending()
    shown = list(mods.values())[:20]
    names = "\n".join(f"- {n[:60]}" for n in shown)
    more = f"\n...and {len(mods) - 20} more" if len(mods) > 20 else ""

    # Disk space estimate: download cache + server copies, per destination drive
    space_note = ""
    try:
        est = await asyncio.to_thread(check_disk_space, mods)
        verdict = "✅ enough free space" if est["ok"] else "⚠️ **DISK SPACE MAY NOT BE ENOUGH**"
        unknown_note = (f" ({len(est['unknown'])} mods reported no size — estimate is low)"
                        if est["unknown"] else "")
        if est["same_drive"]:
            total = est["download_bytes"] + est["install_bytes"]
            space_note = (
                f"\n**Disk estimate ({est['server_drive']}):** downloads: {fmt_gb(est['download_bytes'])} "
                f"+ server copies: {fmt_gb(est['install_bytes'])} = {fmt_gb(total)} needed, "
                f"free: {fmt_gb(est['server_free'])} — {verdict}{unknown_note}\n"
            )
        else:
            space_note = (
                f"\n**Disk estimate:**\n"
                f"- Workshop cache ({est['workshop_drive']}): {fmt_gb(est['download_bytes'])} needed, "
                f"{fmt_gb(est['workshop_free'])} free\n"
                f"- Server copies ({est['server_drive']}): {fmt_gb(est['install_bytes'])} needed, "
                f"{fmt_gb(est['server_free'])} free\n"
                f"{verdict}{unknown_note}\n"
            )
    except Exception as e:
        space_note = f"\n(Disk space check failed: {e})\n"

    # If the mods are already on disk we can offer the map list right away
    maps = await asyncio.to_thread(scan_available_maps, mods)
    if maps:
        next_step = (f"**{len(maps)} maps available** — pick one with `/setup_map map:<name>`:\n"
                     f"{format_map_list(maps, budget=900)}"
                     f"**Next:** `/setup_map`, then `/setup_apply restart:true`.")
    elif STEAM_USERNAME:
        next_step = ("**Next:** `/download`, then `/setup_map` to pick the mission/map, "
                     "then `/setup_apply restart:true`.")
    else:
        next_step = ("**Next:** `/setup_script`, run it on the VM, then `/setup_map` to pick "
                     "the mission/map, then `/setup_apply`.")
    msg = f"Parsed **{len(mods)} mods**:\n{names}{more}\n{dropped_note}{space_note}\n{next_step}"
    await interaction.followup.send(msg[:2000])


@client.tree.command(name="setup_script", description="Generate the SteamCMD download script for the uploaded mod list")
async def setup_script(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    cfg = pending_setups.get(interaction.user.id)
    if not cfg or not cfg.get("mods"):
        await interaction.response.send_message("Start a setup first with `/setup` (attach your preset).", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    # Piggyback an update check: already-installed mods with Steam updates get
    # pulled by the same script run.
    update_note = ""
    outdated = []
    try:
        outdated = await asyncio.to_thread(find_outdated_mods)
        if outdated:
            items = "\n".join(f"- {title}" for _, _, title in outdated[:10])
            more = f"\n...and {len(outdated) - 10} more" if len(outdated) > 10 else ""
            update_note = (f"\n🔄 **{len(outdated)} installed mods also have updates** — "
                           f"included in the script; run `/update` after downloading:\n{items}{more}\n")
    except Exception:
        pass

    mod_ids = list(dict.fromkeys(list(cfg["mods"].keys())
                                 + [mid for mid, _, _ in outdated]))
    script = generate_steamcmd_script(mod_ids)
    script_path = os.path.join(PROJECT_ROOT, "download_mods.txt")
    with open(script_path, "w") as f:
        f.write(script)

    await interaction.followup.send(
        f"SteamCMD script written to `{script_path}` ({len(mod_ids)} mods).\n"
        f"Edit the username, then run on the VM:\n"
        f"```powershell\n& \"{STEAMCMD}\" +runscript \"{script_path}\"\n```\n"
        f"Re-run it until no more downloads happen (big mods time out). Then `/setup_apply`."
        f"{update_note}",
        ephemeral=True
    )


@client.tree.command(name="download", description="Download pending setup mods + updates via SteamCMD (needs cached login)")
async def download(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    if not STEAM_USERNAME:
        await interaction.response.send_message(
            "`STEAM_USERNAME` is not set in .env_setup — automated downloads need it "
            "(plus a one-time interactive SteamCMD login on the machine). "
            "Use `/setup_script` for the manual route.", ephemeral=True)
        return
    if not os.path.isfile(STEAMCMD):
        await interaction.response.send_message(
            f"SteamCMD not found at `{STEAMCMD}`.", ephemeral=True)
        return

    # Collect: pending setup mods + updates of installed mods
    cfg = pending_setups.get(interaction.user.id)
    mod_ids = list(cfg["mods"].keys()) if cfg and cfg.get("mods") else []
    outdated = []
    try:
        outdated = await asyncio.to_thread(find_outdated_mods) if load_last_setup() else []
    except Exception:
        pass
    mod_ids = list(dict.fromkeys(mod_ids + [mid for mid, _, _ in outdated]))
    if not mod_ids:
        await interaction.response.send_message(
            "Nothing to download — no pending setup (`/setup`) and no outdated mods.",
            ephemeral=True)
        return

    await interaction.response.defer()
    remaining = await run_steamcmd_download(mod_ids, interaction.followup)
    if remaining:
        await interaction.followup.send(
            f"⚠️ **{len(remaining)} mods still missing** after all attempts "
            f"(SteamCMD login may have expired — run it interactively once on the VM). "
            f"Missing ids: {', '.join(remaining[:10])}")
        return
    if cfg:
        maps = await asyncio.to_thread(scan_available_maps, cfg["mods"])
        if maps:
            await interaction.followup.send(
                f"✅ All {len(mod_ids)} mods downloaded.\n\n"
                f"**{len(maps)} maps available** — pick one with `/setup_map map:<name>`:\n"
                f"{format_map_list(maps)}"
                f"**Next:** `/setup_map`, then `/setup_apply restart:true`."[:2000])
        else:
            await interaction.followup.send(
                f"✅ All {len(mod_ids)} mods downloaded.\n"
                f"**Next:** `/setup_apply restart:true` (no map choice found for this mod set).")
    else:
        await interaction.followup.send(
            f"✅ All {len(mod_ids)} mods downloaded. **Next:** `/update`.")


@client.tree.command(name="setup_map", description="Choose the mission/map for the pending setup")
@app_commands.describe(map="Start typing to search the maps this mod set offers")
async def setup_map(interaction: discord.Interaction, map: str | None = None):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    cfg = pending_setups.get(interaction.user.id)
    applied = load_last_setup()
    if not cfg and not applied:
        await interaction.response.send_message("No pending setup. Run `/setup` first.", ephemeral=True)
        return
    # Without a pending setup, change the map of the already applied setup
    mod_source = cfg["mods"] if cfg else applied
    maps = await asyncio.to_thread(scan_available_maps, mod_source)
    if not maps:
        # Templates are read from the mission mod's files — they only become
        # visible once the mods are on disk. Without a pending setup there is
        # nothing left to download, so only the applied set can be reported on.
        pending_dl = [mid for mid in mod_source if not workshop_ready(mid)]
        if cfg and pending_dl:
            await interaction.response.send_message(
                f"Maps can only be listed once the mods are downloaded "
                f"({len(pending_dl)} still missing).\n"
                f"**Next:** {'`/download`' if STEAM_USERNAME else '`/setup_script`, run it on the VM'}, "
                f"then `/setup_map` again.", ephemeral=True)
            return
        if cfg:
            await interaction.response.send_message(
                f"No mission templates found for this mod set — default `{cfg['template']}` will be used. "
                f"Continue with `/setup_apply`.", ephemeral=True)
        else:
            missing = f" ({len(pending_dl)} of them no longer in the workshop cache)" if pending_dl else ""
            await interaction.response.send_message(
                f"No mission templates found in the applied mod set{missing}, so there is nothing "
                f"to choose from. Edit the `template =` line in `server.cfg` by hand, or start a "
                f"new setup with `/setup`.", ephemeral=True)
        return

    if map:
        template = maps.get(map, map if map in maps.values() else None)
        if not template:
            await interaction.response.send_message(
                f"`{map}` is not one of the {len(maps)} available maps — pick one from the suggestions.",
                ephemeral=True)
            return

        # A mission for a terrain that isn't installed crashes the server at init
        world = template.split(".", 1)[1]
        folders = (list(build_folder_map(cfg["mods"]).values()) if cfg
                   else list(applied.values()))
        await interaction.response.defer(ephemeral=True)
        has_world = await asyncio.to_thread(world_available, world, folders)
        if has_world is False:
            await interaction.followup.send(
                f"⛔ **`{world}` is not provided by any mod in this setup** — the server would "
                f"crash on startup (`No entry CfgWorlds/{world}`).\n"
                f"Add the terrain mod for it to your preset, or pick a map you have. "
                f"Vanilla worlds and everything from CUP Terrains are safe.")
            return
        note = ("" if has_world else
                f"\n_Could not verify the terrain for `{world}` is installed — "
                f"if the server crashes on start, that's why._")

        if cfg:
            cfg["template"] = template
            cfg["template_chosen"] = True
            persist_pending()
            await interaction.followup.send(
                f"Mission template set to **{template}**.{note}\n"
                f"**Next:** `/setup_apply restart:true`.")
            return

        # No pending setup: rewrite the template in the live server.cfg.
        # Stop first so the change can't race a running server.
        was_running = "RUNNING" in await a_service_state(SERVICE_NAME)
        if was_running:
            await interaction.followup.send("⏹️ Stopping the server to change the map...")
            if not await stop_server_and_wait():
                await interaction.followup.send(
                    "⚠️ Server did not stop within 2 minutes — map not changed.")
                return
        # Snapshot config + saves first: a map change starts a different campaign
        map_backup = await asyncio.to_thread(make_backup, "auto_pre_mapchange")
        await interaction.followup.send(f"💾 Backup: `{os.path.basename(map_backup)}`")
        try:
            with open(SERVER_CFG_PATH, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            new_content, n = re.subn(r'(template\s*=\s*")[^"]*(")',
                                     lambda m: m.group(1) + template + m.group(2),
                                     content, count=1)
            if not n:
                await interaction.followup.send(
                    "⚠️ Could not find a `template =` line in server.cfg — change it manually.")
                return
            await asyncio.to_thread(write_text_atomic, SERVER_CFG_PATH, new_content)
        except OSError as e:
            await interaction.followup.send(f"⚠️ Could not update server.cfg: {e}")
            return
        await interaction.followup.send(f"🗺️ Map changed to **{template}** in `server.cfg`.{note}")
        if was_running:
            await start_server(interaction.followup, verb="restarted with the new map")
        else:
            await interaction.followup.send("**Next:** `/start` to bring the server up.")
        return

    if len(maps) <= 25:
        await interaction.response.send_message(
            "Choose the mission/map:", view=MapSelectView(maps), ephemeral=True)
    else:
        await interaction.response.send_message(
            f"This mod set offers **{len(maps)} maps** — too many for a dropdown.\n"
            f"Run `/setup_map map:` and start typing to search (e.g. `Altis`, `chernarus`, `takistan`).\n"
            f"_Note: these are the maps the mission supports. The matching **terrain** must also be "
            f"installed — vanilla (Altis/Stratis/Malden/Tanoa/Enoch) and CUP Terrains maps "
            f"(chernarus, takistan, sara, …) are covered by your mod set; others need their own map mod._",
            ephemeral=True)


@setup_map.autocomplete("map")
async def setup_map_autocomplete(interaction: discord.Interaction, current: str):
    if not is_authorized(interaction):
        return []
    cfg = pending_setups.get(interaction.user.id)
    if not cfg:
        return []
    maps = await asyncio.to_thread(scan_available_maps, cfg["mods"])
    cur = current.strip().lower()
    if not cur:
        # No input yet: surface the maps that work without extra terrain mods
        common = ["Altis", "Stratis", "Malden", "Tanoa", "Enoch", "enoch"]
        ordered = ([m for m in maps if m in common]
                   + [m for m in maps if m not in common])
    else:
        starts = [m for m in maps if m.lower().startswith(cur)]
        contains = [m for m in maps if cur in m.lower() and m not in starts]
        ordered = starts + contains
    return [app_commands.Choice(name=label[:100], value=label[:100])
            for label in ordered[:25]]


@client.tree.command(name="setup_apply", description="Apply the pending setup (auto-backup first)")
@app_commands.describe(restart="Restart the server immediately after applying")
async def setup_apply(interaction: discord.Interaction, restart: bool = False):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    cfg = pending_setups.get(interaction.user.id)
    if not cfg:
        await interaction.response.send_message("No pending setup. Run `/setup` first.", ephemeral=True)
        return
    # Don't silently apply the default map when real choices exist
    if not cfg.get("template_chosen"):
        maps = await asyncio.to_thread(scan_available_maps, cfg["mods"])
        if len(maps) > 25:
            await interaction.response.send_message(
                f"No mission/map chosen yet — this mod set offers **{len(maps)} maps**.\n"
                f"Pick one with `/setup_map map:` (type to search), then run `/setup_apply` again.",
                ephemeral=True)
            return
        if maps:
            await interaction.response.send_message(
                "No mission/map chosen yet — pick one below, then run `/setup_apply` again.",
                view=MapSelectView(maps), ephemeral=True)
            return
    await interaction.response.defer()

    # 1. Refuse to apply if mods are missing — NSSM params referencing
    # non-existent @Folders would prevent the server from starting.
    # Checked BEFORE the backup so "nothing was changed" is literally true.
    if cfg["mods"]:
        folder_map = build_folder_map(cfg["mods"])
        not_ready = [mid for mid in cfg["mods"]
                     if not workshop_ready(mid)
                     and not os.path.isdir(os.path.join(SERVER_BASE, folder_map[mid]))]
        if not_ready:
            names_missing = "\n".join(f"- {cfg['mods'][mid]} ({mid})" for mid in not_ready[:15])
            more = f"\n...and {len(not_ready) - 15} more" if len(not_ready) > 15 else ""
            await interaction.followup.send(
                f"⚠️ **{len(not_ready)} mods are not downloaded yet — nothing was changed:**\n"
                f"{names_missing}{more}\n"
                f"**Next:** `/download` (or `/setup_script` on the VM), then `/setup_apply` again.")
            return

    # 2. Validate the passwords before anything is written. An unset or
    #    placeholder admin password would go live as the #login secret, and a
    #    server password the HC cannot pass on its command line would leave the
    #    headless client permanently unable to join.
    admin_pw = sanitize_cfg_value(ADMIN_PASSWORD)
    if not admin_pw or admin_pw.lower() in PLACEHOLDER_ADMIN_PASSWORDS:
        await interaction.followup.send(
            "⛔ **`ADMIN_PASSWORD` is unset or still the example placeholder — nothing was "
            "changed.**\nIt becomes the server's `#login` password, so anyone who knows the "
            "default would get admin on the running server. Set a real one in `.env_setup` "
            "and run `/setup_apply` again.")
        return
    server_pw = sanitize_cfg_value(SERVER_PASSWORD)
    if server_pw != sanitize_param_value(server_pw):
        await interaction.followup.send(
            "⛔ **`SERVER_PASSWORD` contains characters the headless client cannot pass on "
            "its command line (spaces, quotes or backslashes) — nothing was changed.**\n"
            "The server would accept it while the HC was rejected and never joined. "
            "Use a password of letters, digits and simple punctuation in `.env_setup`.")
        return

    # 3. Always back up the current state before changing anything
    backup_folder = await asyncio.to_thread(make_backup, "auto_pre_setup")

    # 4. Install mods (copy from workshop dir; never deletes existing folders)
    installed, missing, id_to_folder = [], [], {}
    if cfg["mods"]:
        await interaction.followup.send(
            f"⏳ Backup done (`{os.path.basename(backup_folder)}`). "
            f"Copying up to {len(cfg['mods'])} mods to the server folder — "
            f"this can take several minutes for large mod sets...")
        installed, missing, id_to_folder = await asyncio.to_thread(
            install_mods_from_workshop, cfg["mods"])

    # 5. Write server.cfg (atomic + UTF-8: a server name with an emoji must not
    #    truncate the config half-way through the write)
    server_cfg = DEFAULT_SERVER_CFG.format(
        hostname=sanitize_cfg_value(cfg["hostname"]),
        password=server_pw,
        passwordAdmin=admin_pw,
        maxPlayers=cfg["maxPlayers"],
        template=cfg["template"],
    )
    await asyncio.to_thread(write_text_atomic, SERVER_CFG_PATH, server_cfg)

    # 6. Update NSSM params
    nssm_note = ""
    if id_to_folder:
        mod_string = ";".join(sorted(id_to_folder.values()))
        # The HC runs the full mod set. A reduced list breaks it as soon as the
        # mission references anything missing — most commonly the terrain mod,
        # which makes the HC fail with "Missing addons detected" and never join.
        hc_mods = mod_string
        server_params = f"-port=2302 -config=server.cfg -profiles=profiles -name=server -mod={mod_string}"
        # The HC must present exactly the password server.cfg sets. NSSM splits
        # AppParameters on whitespace, so a password that cannot survive as a
        # bare argument is reported instead of being silently altered.
        hc_params = (f"-client -connect=127.0.0.1 -port=2302 -password={server_pw} "
                     f"-profiles=profiles_hc -name=hc -noSteam -mod={hc_mods}")
        ok1 = set_nssm_params(SERVICE_NAME, server_params)
        ok2 = set_nssm_params(HC_SERVICE_NAME, hc_params)
        nssm_note = "NSSM params updated." if ok1 and ok2 else "⚠️ NSSM update failed — check manually!"
        save_last_setup(id_to_folder, names=cfg["mods"])
        # Keys now match exactly the loaded mod set (stale keys removed)
        await asyncio.to_thread(rebuild_keys, sorted(id_to_folder.values()))
    else:
        nssm_note = "No mods in setup — NSSM params unchanged."

    del pending_setups[interaction.user.id]
    persist_pending()

    await interaction.followup.send(
        f"✅ **Setup applied**\n"
        f"**Backup:** `{os.path.basename(backup_folder)}`\n"
        f"**Template:** {cfg['template']}\n"
        f"**Mods installed/present:** {len(installed)}\n"
        f"{nssm_note}\n\n"
        + ("Restarting now..." if restart else "Restart the server with `/restart` to go live.")
    )
    if restart:
        await do_restart(interaction.followup)


@client.tree.command(name="backup", description="Back up current server setup and saves")
@app_commands.describe(name="Name for this backup")
async def backup(interaction: discord.Interaction, name: str):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    await interaction.response.defer()
    folder = await asyncio.to_thread(make_backup, name)
    size_mb = await asyncio.to_thread(collect_saves_size) / 1024 / 1024
    with open(os.path.join(folder, "profile.json")) as f:
        stored = json.load(f)
    mods_n = len(mod_folders_from_params(stored.get("server_params", "")))
    warn = ("\n⚠️ Could not read the service parameters — this backup only covers "
            "server.cfg and saves." if mods_n == 0 else "")
    await interaction.followup.send(
        f"💾 Backup created: `{os.path.basename(folder)}`\n"
        f"Contains: NSSM params (server + HC, {mods_n} mods), server.cfg, saves ({size_mb:.1f} MB).{warn}"
    )


@client.tree.command(name="profiles", description="List stored backup profiles")
async def profiles(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    entries = list_profiles()
    if not entries:
        await interaction.response.send_message("No profiles stored yet. Create one with `/backup`.")
        return
    lines = []
    for p in entries[-15:]:
        folders = mod_folders_from_params(p.get("server_params", ""))
        # Mission template + map from the stored server.cfg
        template = "?"
        cfg_path = os.path.join(PROFILES_STORE, p["folder"], "server.cfg")
        if os.path.isfile(cfg_path):
            with open(cfg_path, errors="ignore") as f:
                m = re.search(r'template\s*=\s*"([^"]+)"', f.read())
                if m:
                    template = m.group(1)
        mission_mod = next((f for f in folders if "antistasi" in f.lower()), "?")
        lines.append(f"- `{p['folder']}`\n"
                     f"  {template} · {len(folders)} mods · mission: {mission_mod}")
    msg = "**Stored profiles:**\n" + "\n".join(lines)
    await interaction.response.send_message(msg[:2000])


@client.tree.command(name="restore", description="Restore a stored profile (NSSM params, server.cfg, saves)")
@app_commands.describe(folder="Profile folder name from /profiles")
async def restore(interaction: discord.Interaction, folder: str):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    await interaction.response.defer()

    # Validate BEFORE the safety backup so a typo doesn't create junk backups
    valid = {p["folder"] for p in await asyncio.to_thread(list_profiles)}
    if folder not in valid:
        await interaction.followup.send(f"⚠️ Profile `{folder}` not found — see `/profiles`.")
        return

    # Stop first: the saves and server.cfg being replaced are exactly the files
    # a running server owns, and it would overwrite the restored saves on its
    # next autosave.
    was_running = "RUNNING" in await a_service_state(SERVICE_NAME)
    if was_running:
        await interaction.followup.send("⏹️ Stopping the server before restoring...")
        if not await stop_server_and_wait():
            await interaction.followup.send(
                "⚠️ Server did not stop within 2 minutes — **nothing was restored**. "
                "Stop it manually and run `/restore` again.")
            return

    # Safety net: back up current state before restoring over it
    pre = await asyncio.to_thread(make_backup, "auto_pre_restore")

    ok, detail = await asyncio.to_thread(restore_profile, folder)
    if not ok:
        await interaction.followup.send(f"⚠️ {detail}")
        return
    await interaction.followup.send(
        f"✅ Restored `{folder}` ({detail}).\n"
        f"Current state was backed up to `{os.path.basename(pre)}` first.\n"
        + ("**Next:** `/start` to bring the server back up."
           if was_running else "**Next:** `/start` to go live.")
    )


@restore.autocomplete("folder")
async def restore_autocomplete(interaction: discord.Interaction, current: str):
    if not is_authorized(interaction):
        return []
    entries = list_profiles()
    return [
        app_commands.Choice(name=p["folder"][:100], value=p["folder"][:100])
        for p in entries if current.lower() in p["folder"].lower()
    ][:25]


@client.tree.command(name="delete_profile", description="Delete a stored backup profile")
@app_commands.describe(folder="Profile folder name from /profiles",
                       confirm="Must be true — the profile's saves are deleted too")
async def delete_profile(interaction: discord.Interaction, folder: str, confirm: bool = False):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    valid = {p["folder"] for p in await asyncio.to_thread(list_profiles)}
    if folder not in valid:
        await interaction.response.send_message(
            f"⚠️ Profile `{folder}` not found — see `/profiles`.", ephemeral=True)
        return
    if not confirm:
        await interaction.response.send_message(
            f"This permanently deletes `{folder}` including its saved campaign files.\n"
            f"Re-run with `confirm:true` if you're sure.", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        await asyncio.to_thread(shutil.rmtree, os.path.join(PROFILES_STORE, folder))
    except OSError as e:
        await interaction.followup.send(f"⚠️ Could not delete `{folder}`: {e}")
        return
    await interaction.followup.send(f"🗑️ Deleted profile `{folder}`.")


@delete_profile.autocomplete("folder")
async def delete_profile_autocomplete(interaction: discord.Interaction, current: str):
    if not is_authorized(interaction):
        return []
    entries = await asyncio.to_thread(list_profiles)
    return [app_commands.Choice(name=p["folder"][:100], value=p["folder"][:100])
            for p in entries if current.lower() in p["folder"].lower()][:25]


@client.tree.command(name="cleanup_workshop",
                     description="Delete workshop download copies of mods that are installed on the server")
async def cleanup_workshop_cmd(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    if not load_last_setup():
        await interaction.response.send_message(
            "No applied setup recorded — run `/setup_apply` first so I know which "
            "workshop folders belong to installed mods.", ephemeral=True)
        return
    await interaction.response.defer()
    removed, freed, skipped = await asyncio.to_thread(cleanup_workshop)
    skipped_note = ""
    if skipped:
        items = "\n".join(f"- {s}" for s in skipped[:10])
        skipped_note = f"\n⚠️ Skipped (not installed on server):\n{items}"
    await interaction.followup.send(
        f"🧹 Removed **{removed}** workshop download folders, freed **{fmt_gb(freed)}**.{skipped_note}\n"
        f"Note: re-applying a setup later will need these mods re-downloaded."
    )


@client.tree.command(name="checkupdates", description="Check Steam for updates to the installed mods")
async def checkupdates(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    if not load_last_setup():
        await interaction.response.send_message(
            "No applied setup recorded — run `/setup_apply` first.", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        outdated = await asyncio.to_thread(find_outdated_mods)
    except Exception as e:
        await interaction.followup.send(f"⚠️ Update check failed: {e}")
        return
    if not outdated:
        await interaction.followup.send("✅ All installed mods are up to date.")
        return
    items = "\n".join(f"- **{title[:80]}** (`{folder}`)" for _, folder, title in outdated[:20])
    more = f"\n...and {len(outdated) - 20} more" if len(outdated) > 20 else ""
    await interaction.followup.send(
        (f"🔄 **{len(outdated)} mods have updates on Steam:**\n{items}{more}\n\n"
         f"**Next:** `/update` to download, install and restart in one go.")[:2000]
    )


@client.tree.command(name="update", description="Update all installed mods: check, download, install, restart")
@app_commands.describe(restart="Restart the server after updating (default: yes)")
async def update(interaction: discord.Interaction, restart: bool = True):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    if not load_last_setup():
        await interaction.response.send_message(
            "No applied setup recorded — run `/setup_apply` first.", ephemeral=True)
        return
    await interaction.response.defer()

    # 1. Check
    try:
        outdated = await asyncio.to_thread(find_outdated_mods)
    except Exception as e:
        await interaction.followup.send(f"⚠️ Update check failed: {e}")
        return
    if not outdated:
        await interaction.followup.send("✅ All installed mods are up to date.")
        return
    items = "\n".join(f"- **{title[:80]}**" for _, _, title in outdated[:15])
    more = f"\n...and {len(outdated) - 15} more" if len(outdated) > 15 else ""
    await interaction.followup.send(
        f"🔄 Updating **{len(outdated)} mods:**\n{items}{more}"[:2000])

    # 2. Download into the cache (automated route only)
    outdated_ids = [mid for mid, _, _ in outdated]
    if STEAM_USERNAME and os.path.isfile(STEAMCMD):
        remaining = await run_steamcmd_download(outdated_ids, interaction.followup)
        if remaining:
            await interaction.followup.send(
                f"⚠️ {len(remaining)} mods failed to download — aborting before install. "
                f"Try `/update` again (SteamCMD login may have expired).")
            return

    # 3. Stop the server before replacing mod folders. A running Arma holds its
    #    .pbo files open, so installing underneath it either fails outright or
    #    pulls the addons out from under the live session.
    was_running = "RUNNING" in await a_service_state(SERVICE_NAME)
    if was_running:
        await interaction.followup.send("⏹️ Stopping the server to install the updates...")
        if not await stop_server_and_wait():
            await interaction.followup.send(
                "⚠️ Server did not stop within 2 minutes — **nothing was installed**. "
                "Stop it manually and run `/update` again.")
            return

    # 4. Install
    await interaction.followup.send("⏳ Installing updates — this can take a few minutes...")
    updated, needs_download, failed = await asyncio.to_thread(
        update_installed_mods, outdated_ids)
    if updated:
        # Refresh keys for the full current mod set (removes stale ones too)
        await asyncio.to_thread(rebuild_keys, sorted(load_last_setup().values()))
    parts = []
    if updated:
        parts.append(f"✅ Updated: {', '.join(f'`{u}`' for u in updated[:15])}")
    if needs_download:
        dl = "\n".join(f"- {m}" for m in needs_download[:10])
        parts.append(f"⚠️ Not in workshop cache (no `STEAM_USERNAME` for auto-download?) — "
                     f"run `/setup_script` output on the VM, then `/update` again:\n{dl}")
    if failed:
        fl = "\n".join(f"- {m}" for m in failed[:10])
        parts.append(f"⛔ **{len(failed)} mods could not be replaced** (their previous copy was "
                     f"kept, so the server still starts):\n{fl}")

    # 5. Restart
    do_it = restart and updated and not needs_download and not failed
    if not do_it:
        parts.append(f"Run `/start` to bring the server back up."
                     if was_running else "Run `/start` to load the updated mods.")
    await interaction.followup.send("\n".join(parts)[:2000])
    if do_it:
        await start_server(interaction.followup, verb="restarted with the updated mods")


@client.tree.command(name="preset", description="Export the server's current mod list as a launcher preset")
@app_commands.describe(name="Preset name shown in the Arma 3 launcher")
async def preset(interaction: discord.Interaction, name: str = "Server Mods"):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    if not load_last_setup():
        await interaction.response.send_message(
            "No applied setup recorded — run `/setup_apply` first.", ephemeral=True)
        return
    await interaction.response.defer()

    # Setups applied before names were recorded: fetch titles from Steam
    if not load_last_names():
        try:
            details = await asyncio.to_thread(fetch_mod_details, list(load_last_setup().keys()))
            fetched = {mid: d["title"] for mid, d in details.items() if d.get("title")}
            if fetched:
                await asyncio.to_thread(save_last_setup, load_last_setup(), fetched)
        except Exception:
            pass

    html, count = await asyncio.to_thread(build_preset_html, sanitize_cfg_value(name) or "Server Mods")
    if not count:
        await interaction.followup.send("⚠️ No mods found in the current setup.")
        return
    path = os.path.join(PROJECT_ROOT, "server_preset.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    await interaction.followup.send(
        f"📋 Current server mod list — **{count} mods**.\n"
        f"Players can import this in the Arma 3 launcher: **MODS → PRESET → IMPORT**.",
        file=discord.File(path, filename="server_preset.html"))


@client.tree.command(name="help", description="Shows all commands and the typical workflows")
async def help_cmd(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    dl = "`/download`" if STEAM_USERNAME else "`/setup_script` (run its output on the VM)"
    await interaction.response.send_message(
        "**🖥️ Server management**\n"
        "`/start` `/stop` `/restart` — control the server (HC restarts automatically)\n"
        "`/status` `/uptime` `/players` — monitoring\n\n"
        "**🛠️ New server setup** (in this order)\n"
        f"1. `/setup` — attach your launcher preset `.html` (optional: name, max players)\n"
        f"2. {dl} — fetch the mods\n"
        f"3. `/setup_apply restart:true` — asks for the mission/map, then installs, configures, goes live\n"
        f"(server/admin passwords come from `.env_setup`, not Discord)\n\n"
        "**🔄 Mod updates**\n"
        f"`/checkupdates` — see what's outdated\n"
        f"`/update` — checks, downloads, installs and restarts in one command\n\n"
        "**💾 Backups**\n"
        "`/backup <name>` — snapshot config + saves\n"
        "`/profiles` — list snapshots · `/restore <folder>` — roll back (auto-backup first)\n"
        "`/delete_profile <folder> confirm:true` — remove a snapshot\n\n"
        "**📋 Sharing**\n"
        "`/preset` — export the server's current mod list as a launcher preset for players\n\n"
        "**🧹 Housekeeping**\n"
        "`/cleanup_workshop` — free disk space (deletes download cache of installed mods)",
        ephemeral=True
    )


@client.event
async def on_ready():
    print(f"A3Bot logged in as {client.user} (ID: {client.user.id})")


client.run(BOT_TOKEN)
