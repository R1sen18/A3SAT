"""Installed-mod version tracking for A3Bot (discord-free so it can be unit tested).

Answers three questions with nothing but the file system and plain data:

- Which version of a mod is installed in a server @Folder? Recorded per mod in
  last_setup.json as the Steam ``time_updated`` of the copy that was installed.
- Which version sits in the SteamCMD workshop cache? SteamCMD records it in
  ``steamapps\\workshop\\appworkshop_107410.acf`` (``timeupdated``).
- May a server @Folder be replaced by the cached copy? Only by a provably newer
  copy of the same workshop item, swapped in so a failure never leaves the mod
  missing.
"""
import os
import re
import shutil
from typing import NamedTuple


# ------------------------------------------------------------ applied-setup state

def parse_state(data) -> dict:
    """Normalise the contents of last_setup.json.

    Returns ``{"applied_at": int, "mods": {id: folder}, "names": {id: name},
    "installed": {id: {"folder": str, "time_updated": int}}}``. Accepts every
    format ever written: the original flat ``{id: folder}`` dict, the later
    ``{"applied_at", "mods", "names"}`` one without per-mod versions, and
    garbage (which yields an empty state rather than an exception)."""
    if not isinstance(data, dict):
        data = {}
    mods = data.get("mods", data)
    mods = {str(k): v for k, v in mods.items()
            if isinstance(v, str)} if isinstance(mods, dict) else {}
    names = data.get("names")
    names = names if isinstance(names, dict) else {}
    installed = {}
    raw = data.get("installed")
    if isinstance(raw, dict):
        for mid, rec in raw.items():
            if not isinstance(rec, dict) or not isinstance(rec.get("folder"), str):
                continue
            try:
                ts = int(rec.get("time_updated"))
            except (TypeError, ValueError):
                continue
            installed[str(mid)] = {"folder": rec["folder"], "time_updated": ts}
    try:
        applied_at = int(data.get("applied_at", 0))
    except (TypeError, ValueError):
        applied_at = 0
    return {"applied_at": applied_at, "mods": mods, "names": names,
            "installed": installed}


def installed_version(installed: dict, mod_id: str, folder: str) -> int | None:
    """Steam time_updated of the copy installed in ``folder`` for ``mod_id``,
    or None when unknown. A record made for a different folder name does not
    count — that folder may hold a different copy entirely."""
    rec = installed.get(mod_id)
    if rec and rec.get("folder") == folder:
        return rec["time_updated"]
    return None


def merge_installed(previous: dict, id_to_folder: dict[str, str],
                    updates: dict[str, int] | None = None) -> dict:
    """Per-mod version records for a new state file: records for mods that are
    still mapped to the same folder are carried over, ``updates`` ({id: ts}
    for folders just copied) replace them, and records for mods no longer in
    the setup are dropped."""
    out = {}
    for mid, folder in id_to_folder.items():
        ts = installed_version(previous, mid, folder)
        if ts is not None:
            out[mid] = {"folder": folder, "time_updated": ts}
    for mid, ts in (updates or {}).items():
        if mid in id_to_folder and ts is not None:
            out[mid] = {"folder": id_to_folder[mid], "time_updated": int(ts)}
    return out


# --------------------------------------------------------- SteamCMD's .acf record

_VDF_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*|\s+|(.)', re.S)


def parse_vdf(text: str) -> dict:
    """Parse Valve KeyValues text (the .acf/.vdf format) into nested dicts.
    Malformed input yields whatever was parsed up to that point."""
    root: dict = {}
    stack = [root]
    key = None
    for m in _VDF_TOKEN.finditer(text):
        string, brace, junk = m.group(1), m.group(2), m.group(3)
        if string is not None:
            value = string.replace('\\"', '"').replace("\\\\", "\\")
            if key is None:
                key = value
            else:
                stack[-1][key] = value
                key = None
        elif brace == "{":
            if key is None:
                break  # a block without a name — stop rather than guess
            child: dict = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) == 1:
                break
            stack.pop()
            key = None
        elif junk is not None:
            break
    return root


def acf_path_for(workshop_base: str) -> str:
    """``...\\steamapps\\workshop\\content\\107410`` →
    ``...\\steamapps\\workshop\\appworkshop_107410.acf``."""
    base = os.path.normpath(workshop_base)
    app_id = os.path.basename(base)
    return os.path.join(os.path.dirname(os.path.dirname(base)), f"appworkshop_{app_id}.acf")


def read_cache_versions(acf_path: str) -> dict[str, int]:
    """{mod_id: timeupdated} of the items SteamCMD has installed in its
    workshop cache; {} when the file is missing or unreadable."""
    try:
        with open(acf_path, encoding="utf-8", errors="replace") as f:
            data = parse_vdf(f.read())
    except OSError:
        return {}
    state = next(iter(data.values()), {}) if data else {}
    items = state.get("WorkshopItemsInstalled", {}) if isinstance(state, dict) else {}
    out = {}
    if isinstance(items, dict):
        for mid, rec in items.items():
            if not isinstance(rec, dict):
                continue
            try:
                out[str(mid)] = int(rec.get("timeupdated"))
            except (TypeError, ValueError):
                continue
    return out


# ------------------------------------------------------------- folder identity

_PUBLISHED_ID = re.compile(r"^\s*publishedid\s*=\s*(\d+)\s*;", re.M | re.I)


def read_published_id(mod_dir: str) -> str | None:
    """Workshop id from a mod folder's meta.cpp (written by the Steam/launcher
    download), or None when the folder has no readable meta.cpp."""
    try:
        with open(os.path.join(mod_dir, "meta.cpp"), encoding="utf-8", errors="ignore") as f:
            m = _PUBLISHED_ID.search(f.read(64 * 1024))
    except OSError:
        return None
    return m.group(1) if m else None


# ------------------------------------------------------------------- decisions

# Decision / RefreshResult kinds
NEWER = "newer"            # cache copy is newer: replace
CURRENT = "current"        # installed copy is at least as new: nothing to do
NOT_NEWER = "not_newer"    # cannot prove the cache copy newer: download first
MISMATCH = "mismatch"      # meta.cpp names another workshop item: never touch
FAILED = "failed"          # swap failed; previous copy kept


class Decision(NamedTuple):
    replace: bool
    note: str  # "" when there is nothing worth telling the admin
    kind: str


def decide_refresh(mod_id: str, installed_ts: int | None, cache_ts: int | None,
                   steam_ts: int | None = None, folder_id: str | None = None,
                   cache_id: str | None = None) -> Decision:
    """Whether an existing server @Folder may be replaced by the cached copy.

    Replacing is allowed only for a newer copy of the same workshop item:
    - meta.cpp of either side naming a different item → never;
    - cache version unknown (no SteamCMD record) → never, it cannot be ranked;
    - installed version known → only when the cache copy is strictly newer;
    - installed version unknown (setups applied before versions were recorded)
      → only when the cache copy is Steam's current version, so the swap
      cannot be a downgrade whatever the folder holds.
    """
    if folder_id and folder_id != mod_id:
        return Decision(False, f"folder holds workshop item {folder_id}, not {mod_id} — left alone",
                        MISMATCH)
    if cache_id and cache_id != mod_id:
        return Decision(False, f"workshop cache folder holds item {cache_id} — left alone",
                        MISMATCH)
    if cache_ts is None:
        return Decision(False, "workshop copy has no SteamCMD version record — "
                               "cannot tell whether it is newer", NOT_NEWER)
    if installed_ts is not None:
        if cache_ts > installed_ts:
            return Decision(True, "", NEWER)
        return Decision(False, "", CURRENT)
    if steam_ts is not None and cache_ts >= steam_ts:
        return Decision(True, "", NEWER)
    return Decision(False, "installed version unknown and the workshop copy is not "
                           "Steam's latest — run `/download` first", NOT_NEWER)


def is_outdated(installed_ts: int | None, steam_ts: int | None) -> bool:
    """Whether Steam has a newer version than the one installed. Mods Steam
    does not report on are never flagged; mods with no recorded installed
    version are (a legacy state cannot prove they are current)."""
    if steam_ts is None:
        return False
    if installed_ts is None:
        return True
    return steam_ts > installed_ts


def stale_cache_ids(mod_ids, steam_versions: dict[str, int],
                    cache_versions: dict[str, int]) -> list[str]:
    """Mods present in the workshop cache that Steam has a newer version of —
    SteamCMD must fetch them again even though their folder exists."""
    out = []
    for mid in mod_ids:
        steam = steam_versions.get(mid)
        cached = cache_versions.get(mid)
        if steam is not None and cached is not None and cached < steam:
            out.append(mid)
    return out


# ---------------------------------------------------------------------- copying

def copy_keys(mod_dir: str, keys_dir: str):
    for root, _, files in os.walk(mod_dir):
        for f in files:
            if f.lower().endswith(".bikey"):
                shutil.copy2(os.path.join(root, f), keys_dir)


def swap_in_copy(src: str, dest: str) -> str | None:
    """Replace ``dest`` with a fresh copy of ``src``. Returns None on success,
    else a short error description — in which case ``dest`` still holds the
    previous copy (or, if even restoring failed, the note says where it is).

    The old copy is moved aside instead of deleted in place: if a running
    server or headless client holds a file open, the move fails before
    anything is lost, where an in-place rmtree would leave a half-deleted mod."""
    stale = f"{dest}.old"
    try:
        if os.path.isdir(stale):
            shutil.rmtree(stale)
        if os.path.isdir(dest):
            os.rename(dest, stale)
    except OSError as e:
        return f"{e.__class__.__name__}: in use by the server or headless client?"
    try:
        shutil.copytree(src, dest)
    except OSError as e:
        if os.path.isdir(stale):
            shutil.rmtree(dest, ignore_errors=True)  # drop the partial copy
            try:
                os.rename(stale, dest)
            except OSError:
                return (f"{e.__class__.__name__}; previous copy could not be put back "
                        f"and is at {stale}")
        return e.__class__.__name__
    shutil.rmtree(stale, ignore_errors=True)
    return None


class RefreshResult(NamedTuple):
    replaced: bool
    note: str  # reason it was not replaced, or the error; "" if nothing to say
    kind: str  # one of the Decision kinds, or FAILED


def refresh_folder(mod_id: str, src: str, dest: str, installed_ts: int | None,
                   cache_ts: int | None, steam_ts: int | None = None,
                   keys_dir: str | None = None) -> RefreshResult:
    """Replace an installed @Folder by the cached copy when — and only when —
    ``decide_refresh`` allows it, then copy the mod's keys."""
    decision = decide_refresh(mod_id, installed_ts, cache_ts, steam_ts,
                              folder_id=read_published_id(dest),
                              cache_id=read_published_id(src))
    if not decision.replace:
        return RefreshResult(False, decision.note, decision.kind)
    error = swap_in_copy(src, dest)
    if error:
        return RefreshResult(False, error, FAILED)
    if keys_dir:
        copy_keys(dest, keys_dir)
    return RefreshResult(True, "", NEWER)
