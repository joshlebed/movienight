"""Torrent parsing and magnet link generation with tracker support."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import quote


class BencodeError(Exception):
    """Error parsing bencode data."""

    pass


def _bdecode(
    data: bytes, i: int = 0, capture_info: bool = False
) -> tuple[Any, int, bytes | None]:
    """Decode bencode, returning (value, next_index, info_slice_bytes or None)."""
    if i >= len(data):
        raise BencodeError("Unexpected end of data")

    c = data[i : i + 1]
    if c == b"i":  # integer
        j = data.index(b"e", i)
        num = int(data[i + 1 : j])
        return num, j + 1, None
    elif c == b"l":  # list
        i += 1
        lst = []
        info_slice = None
        while data[i : i + 1] != b"e":
            v, i, info_sub = _bdecode(data, i, capture_info=capture_info)
            lst.append(v)
            info_slice = info_slice or info_sub
        return lst, i + 1, info_slice
    elif c == b"d":  # dict
        i += 1
        d: dict[bytes, Any] = {}
        info_slice = None
        while data[i : i + 1] != b"e":
            # keys are byte strings
            k, i, _ = _bdecode(data, i, capture_info=False)
            if not isinstance(k, (bytes, bytearray)):
                raise BencodeError("Non-bytes dict key")
            k_bytes = bytes(k)
            # Special handling to capture raw 'info' bencoded slice without re-encode
            if capture_info and k_bytes == b"info":
                val_start = i
                v, i, _ = _bdecode(data, i, capture_info=False)
                val_end = i
                d[k_bytes] = v
                info_slice = data[val_start:val_end]
            else:
                v, i, sub_info = _bdecode(data, i, capture_info=capture_info)
                d[k_bytes] = v
                info_slice = info_slice or sub_info
        return d, i + 1, info_slice
    elif b"0" <= c <= b"9":  # byte string
        j = data.index(b":", i)
        length = int(data[i:j])
        start = j + 1
        end = start + length
        return data[start:end], end, None
    else:
        raise BencodeError(f"Invalid bencode prefix at {i}: {c!r}")


def decode_torrent(torrent_path: Path) -> tuple[dict[bytes, Any], str | None, str]:
    """Parse a torrent file and return (metadata_dict, infohash, display_name).

    Infohash is returned as 'btih:<hex>' for v1 or 'btmh:1220<hex>' for v2.
    """
    raw = torrent_path.read_bytes()
    val, _, info_slice = _bdecode(raw, 0, capture_info=True)
    if not isinstance(val, dict):
        raise BencodeError("Top-level bencode is not a dict")

    info = val.get(b"info")
    name = None
    if isinstance(info, dict):
        nm = info.get(b"name")
        if isinstance(nm, (bytes, bytearray)):
            name = nm.decode("utf-8", errors="replace")

    # Compute infohash
    if info_slice is None:
        return val, None, name or torrent_path.stem

    # Detect v2 (BitTorrent v2 torrents have 'meta version' = 2)
    is_v2 = False
    if isinstance(info, dict):
        try:
            meta_version = info.get(b"meta version")
            if isinstance(meta_version, int) and meta_version == 2:
                is_v2 = True
        except Exception:
            pass

    if is_v2:
        digest = hashlib.sha256(info_slice).hexdigest()
        infohash = f"btmh:1220{digest}"
    else:
        digest = hashlib.sha1(info_slice).hexdigest()
        infohash = f"btih:{digest}"

    return val, infohash, name or torrent_path.stem


def build_magnet(
    torrent_dict: dict[bytes, Any], infohash: str, display_name: str
) -> str | None:
    """Build a magnet link with trackers from torrent metadata.

    Includes:
    - xt: exact topic (infohash)
    - dn: display name
    - tr: tracker URLs from announce and announce-list
    """
    if not infohash:
        return None

    if infohash.startswith("btih:"):
        xt = f"urn:btih:{infohash.split(':', 1)[1]}"
    elif infohash.startswith("btmh:"):
        xt = f"urn:btmh:{infohash.split(':', 1)[1]}"
    else:
        return None

    params = [f"xt={quote(xt, safe=':')}"]
    if display_name:
        params.append(f"dn={quote(display_name)}")

    # Add trackers
    trackers = extract_trackers(torrent_dict)
    for tracker in trackers:
        params.append(f"tr={quote(tracker)}")

    return "magnet:?" + "&".join(params)


def extract_trackers(torrent_dict: dict[bytes, Any]) -> list[str]:
    """Extract unique tracker URLs from torrent metadata."""
    trackers: list[str] = []
    seen: set[str] = set()

    # Single announce URL
    announce = torrent_dict.get(b"announce")
    if isinstance(announce, (bytes, bytearray)):
        url = announce.decode("utf-8", "ignore")
        if url and url not in seen:
            trackers.append(url)
            seen.add(url)

    # Announce list (may be nested per BEP 12)
    announce_list = torrent_dict.get(b"announce-list")
    if isinstance(announce_list, list):
        for item in announce_list:
            if isinstance(item, list):
                for sub_item in item:
                    if isinstance(sub_item, (bytes, bytearray)):
                        url = sub_item.decode("utf-8", "ignore")
                        if url and url not in seen:
                            trackers.append(url)
                            seen.add(url)
            elif isinstance(item, (bytes, bytearray)):
                url = item.decode("utf-8", "ignore")
                if url and url not in seen:
                    trackers.append(url)
                    seen.add(url)

    return trackers


def get_torrent_files(torrent_dict: dict[bytes, Any]) -> set[str]:
    """Extract file names from torrent metadata."""
    info = torrent_dict.get(b"info")
    names: set[str] = set()

    if not isinstance(info, dict):
        return names

    # Single-file mode
    if b"length" in info and b"name" in info:
        try:
            nm = info[b"name"]
            if isinstance(nm, (bytes, bytearray)):
                names.add(Path(nm.decode("utf-8", "ignore")).name)
        except Exception:
            pass

    # Multi-file mode
    if b"files" in info and isinstance(info[b"files"], list):
        for f in info[b"files"]:
            if isinstance(f, dict) and b"path" in f:
                path = f[b"path"]
                # path can be list of path segments per spec
                if isinstance(path, list):
                    segs = []
                    for seg in path:
                        if isinstance(seg, (bytes, bytearray)):
                            segs.append(seg.decode("utf-8", "ignore"))
                    nm = Path(*segs).name if segs else None
                elif isinstance(path, (bytes, bytearray)):
                    nm = Path(path.decode("utf-8", "ignore")).name
                else:
                    nm = None
                if nm:
                    names.add(nm)

    # v2 file tree (BEP 52)
    if b"file tree" in info and isinstance(info[b"file tree"], dict):

        def walk_tree(tree: dict[bytes, Any]) -> None:
            for k, v in tree.items():
                if not isinstance(k, (bytes, bytearray)):
                    continue
                key = k.decode("utf-8", "ignore")
                if isinstance(v, dict):
                    if b"" in v:  # file leaf
                        names.add(key)
                    else:
                        walk_tree(v)

        walk_tree(info[b"file tree"])

    return names


def load_torrents(torrents_dir: Path) -> list[dict[str, Any]]:
    """Load all torrent files from a directory and return parsed data."""
    if not torrents_dir.exists():
        return []

    index: list[dict[str, Any]] = []
    for p in torrents_dir.rglob("*.torrent"):
        try:
            meta, infohash, display_name = decode_torrent(p)
            magnet = build_magnet(meta, infohash, display_name) if infohash else None
            files = get_torrent_files(meta)
            trackers = extract_trackers(meta)
            index.append(
                {
                    "path": str(p),
                    "display_name": display_name,
                    "infohash": infohash,
                    "magnet": magnet,
                    "trackers": trackers,
                    "files": {f.lower() for f in files},
                }
            )
        except Exception:
            # Skip malformed torrents
            pass

    return index


def match_torrent(folder_name: str, torrents: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match a media folder to a torrent by name."""
    dir_name = folder_name.lower()

    # 1) Exact match with torrent display name
    for t in torrents:
        torrent_name = (t["display_name"] or "").lower()
        if torrent_name and torrent_name == dir_name:
            return t

    # 2) Substring match (either contains the other)
    for t in torrents:
        torrent_name = (t["display_name"] or "").lower()
        if torrent_name and (torrent_name in dir_name or dir_name in torrent_name):
            return t

    # 3) Check if any files in the torrent match directory name
    for t in torrents:
        if any(dir_name in f for f in t["files"]):
            return t

    return None
