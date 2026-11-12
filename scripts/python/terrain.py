"""Terrain availability checks for A3Bot (discord-free so it can be unit tested)."""
import os

# Worlds shipped by the base game / official DLC — always present
VANILLA_WORLDS = {"altis", "stratis", "malden", "tanoa", "enoch", "vr",
                  "cam_lao_nam", "spe_normandy", "spe_mortain", "sefrouramal",
                  "gm_weferlingen_summer", "gm_weferlingen_winter"}

# Only positive results are cached (keyed by world + dir set); a False can go
# stale when a mod folder finishes downloading or is installed later.
_world_cache: dict[tuple[str, tuple[str, ...]], bool] = {}

_PRODUCT_METHOD = 0x56657273  # 'Vers' — product entry carrying key/value strings
_MAX_ENTRIES = 1_000_000
_MAX_NAME = 4096


class _BadHeader(Exception):
    pass


def _read_cstring(f) -> bytes:
    """Bytes up to (not including) the next NUL; b"" at EOF."""
    out = bytearray()
    while True:
        c = f.read(1)
        if not c or c == b"\0":
            return bytes(out)
        out += c
        if len(out) > _MAX_NAME:
            raise _BadHeader


def _pbo_header(path: str) -> tuple[list[str], bool]:
    """(file names, complete). complete is False when the header could not be
    read to its terminator (I/O error, garbled/oversized name, entry ceiling,
    or truncation)."""
    names: list[str] = []
    try:
        with open(path, "rb") as f:
            for _ in range(_MAX_ENTRIES):
                name = _read_cstring(f)
                block = f.read(20)
                if len(block) < 20:
                    return names, False
                method = int.from_bytes(block[:4], "little")
                if name == b"":
                    if method == _PRODUCT_METHOD:
                        while _read_cstring(f):  # key/value pairs; only an empty key ends them
                            _read_cstring(f)
                        continue
                    return names, True
                names.append(name.decode("latin-1"))
            return names, False
    except (OSError, _BadHeader):
        return names, False


def pbo_file_names(path: str) -> list[str]:
    """File names listed in a PBO header. Truncated/garbled files yield a
    partial list instead of raising."""
    return _pbo_header(path)[0]


def world_available(world: str, mod_dirs: list[str]) -> bool | None:
    """Is the terrain for `world` provided by a pbo under any of these
    absolute directories? Terrain pbos contain a "worlds/<name>/<name>.wrp" entry.
    Returns None when nothing was scannable on disk, or when no pbo matched but
    some pbo header could not be fully parsed (can't verify)."""
    world_key = world.lower()
    if world_key in VANILLA_WORLDS:
        return True
    key = (world_key, tuple(sorted(d.lower() for d in mod_dirs)))
    if key in _world_cache:
        return _world_cache[key]
    target = f"{world_key}.wrp"
    scanned_any = False
    incomplete = False
    for mod_dir in mod_dirs:
        if not os.path.isdir(mod_dir):
            continue
        for root, _, files in os.walk(mod_dir):
            for fname in files:
                if not fname.lower().endswith(".pbo"):
                    continue
                scanned_any = True
                names, complete = _pbo_header(os.path.join(root, fname))
                incomplete = incomplete or not complete
                for name in names:
                    if name.replace("/", "\\").rsplit("\\", 1)[-1].lower() == target:
                        _world_cache[key] = True
                        return True
    if not scanned_any or incomplete:
        return None
    # Never cache False: the folder may still be downloading/being installed
    return False
