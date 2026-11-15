"""Find the file of a completed torrent on the local disk.

qBittorrent reports ``content_path`` as an absolute path inside its own
container (``/data/getflix/Dune.mkv``). Getflix mounts the same directory at
``GETFLIX_DOWNLOAD_ROOT``, so the reported path can usually be used as is -
after checking that it really lives inside that directory.
"""

from __future__ import annotations

from pathlib import Path

#: Extensions a media player can handle. When a torrent contains several
#: videos the largest one is used.
VIDEO_EXTENSIONS = frozenset(
    {
        ".3gp",
        ".asf",
        ".avi",
        ".divx",
        ".f4v",
        ".flv",
        ".m2ts",
        ".m2v",
        ".m4v",
        ".mk3d",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".mts",
        ".ogm",
        ".ogv",
        ".rmvb",
        ".ts",
        ".vob",
        ".webm",
        ".wmv",
    }
)

#: Content types for the extensions Python does not know (or gets wrong).
CONTENT_TYPES = {
    ".3gp": "video/3gpp",
    ".avi": "video/x-msvideo",
    ".divx": "video/divx",
    ".flv": "video/x-flv",
    ".m2ts": "video/mp2t",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".mts": "video/mp2t",
    ".ogv": "video/ogg",
    ".ts": "video/mp2t",
    ".webm": "video/webm",
    ".wmv": "video/x-ms-wmv",
}


class PlayableFileNotFound(Exception):
    """No playable file could be found for a torrent."""


def content_type_for(path: Path) -> str:
    """The MIME type of ``path``, with a fallback for exotic containers."""
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def resolve_playable_file(
    content_path: str,
    root: str | Path,
    *,
    qbittorrent_root: str | Path | None = None,
) -> Path:
    """Return the video file that belongs to a completed torrent.

    ``root`` is the download directory as Getflix sees it. When Getflix runs
    on the host and qBittorrent in a container the reported path starts with
    a different prefix, ``qbittorrent_root`` rewrites it.
    """
    if not content_path:
        raise PlayableFileNotFound("qBittorrent did not report a file for this torrent")

    root_path = Path(root).expanduser().resolve()
    candidate = Path(content_path).expanduser()

    if not candidate.is_absolute():
        candidate = root_path / candidate
    candidate = candidate.resolve()

    # qBittorrent may report a path from another mount point, for example
    # when it runs in a container and Getflix runs on the host.
    if not _is_inside(candidate, root_path):
        remapped = None
        if qbittorrent_root:
            remapped = _remap(candidate, Path(qbittorrent_root).expanduser(), root_path)
        if remapped is None or not _is_inside(remapped, root_path):
            raise PlayableFileNotFound(
                "The file is outside the download directory (GETFLIX_DOWNLOAD_ROOT)"
            )
        candidate = remapped

    if candidate.is_file():
        if candidate.suffix.lower() not in VIDEO_EXTENSIONS:
            raise PlayableFileNotFound("The download does not contain a video file")
        return candidate
    if candidate.is_dir():
        return _largest_video(candidate, root_path)
    raise PlayableFileNotFound("The file does not exist (anymore), was the download moved?")


def _remap(path: Path, source_root: Path, target_root: Path) -> Path | None:
    """Translate ``path`` from the qBittorrent prefix to the local one."""
    try:
        relative = path.relative_to(source_root.resolve())
    except ValueError:
        return None
    return (target_root / relative).resolve()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _largest_video(directory: Path, root: Path) -> Path:
    """Pick the largest video file of a multi file torrent."""
    best: Path | None = None
    best_size = -1
    for path in directory.rglob("*"):
        if path.suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
            continue
        resolved = path.resolve()
        # Skip symlinks that point outside of the download directory.
        if not _is_inside(resolved, root):
            continue
        try:
            size = resolved.stat().st_size
        except OSError:  # pragma: no cover - the file vanished mid walk
            continue
        if size > best_size:
            best, best_size = resolved, size
    if best is None:
        raise PlayableFileNotFound("The download does not contain a video file")
    return best
