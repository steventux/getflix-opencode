"""Keeps track of search results and the qBittorrent torrents they spawn.

The registry lets the UI show a live progress bar underneath a search result
even though qBittorrent only identifies a torrent by its hash - a value we
do not know until the torrent has been added.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field

from .fuzzy import normalise
from .qbittorrent import SearchHit, Torrent

__all__ = ["TorrentRegistry", "find_torrent", "RegistryEntry"]


def find_torrent(name: str, torrents: Sequence[Torrent]) -> Torrent | None:
    """Find the torrent that belongs to a release name.

    qBittorrent derives the torrent name from the ``.torrent`` metadata, which
    is usually - but not always - identical to the release file name.
    """
    target = normalise(name)
    if not target:
        return None

    for torrent in torrents:
        if normalise(torrent.name) == target:
            return torrent

    if len(target) >= 12:
        for torrent in torrents:
            candidate = normalise(torrent.name)
            if candidate and (candidate.startswith(target) or target.startswith(candidate)):
                return torrent
    return None


@dataclass
class RegistryEntry:
    """A search result we may have started downloading."""

    result_id: str
    name: str = ""
    url: str = ""
    hit: SearchHit | None = None
    torrent_hash: str | None = None
    added: bool = False
    error: str = ""
    extra: dict[str, str] = field(default_factory=dict)


class TorrentRegistry:
    """A small, thread safe LRU cache of search results and their torrents."""

    def __init__(self, max_entries: int = 500) -> None:
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, RegistryEntry] = OrderedDict()
        self._max_entries = max_entries

    def remember(self, hit: SearchHit) -> RegistryEntry:
        with self._lock:
            entry = self._entries.get(hit.id)
            if entry is None:
                entry = RegistryEntry(result_id=hit.id, name=hit.file_name, hit=hit)
                self._entries[hit.id] = entry
            else:
                entry.hit = hit
                entry.name = hit.file_name
            self._entries.move_to_end(hit.id)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return entry

    def get(self, result_id: str) -> RegistryEntry | None:
        with self._lock:
            return self._entries.get(result_id)

    def mark_added(self, result_id: str, name: str = "", url: str = "") -> None:
        with self._lock:
            entry = self._entries.get(result_id)
            if entry is None:
                entry = RegistryEntry(result_id=result_id, name=name, url=url)
                self._entries[result_id] = entry
            entry.added = True
            entry.error = ""
            if name:
                entry.name = name
            if url:
                entry.url = url
            self._entries.move_to_end(result_id)

    def mark_error(self, result_id: str, message: str) -> None:
        with self._lock:
            entry = self._entries.get(result_id)
            if entry is None:
                entry = RegistryEntry(result_id=result_id)
                self._entries[result_id] = entry
            entry.error = message
            entry.added = False

    def bind_hash(self, result_id: str, torrent_hash: str) -> None:
        with self._lock:
            entry = self._entries.get(result_id)
            if entry is not None and torrent_hash:
                entry.torrent_hash = torrent_hash

    def resolve(
        self, result_id: str, torrents: Sequence[Torrent]
    ) -> tuple[RegistryEntry | None, Torrent | None]:
        """Resolve a result id to the torrent that belongs to it."""
        entry = self.get(result_id)
        if entry is None:
            return None, None

        if entry.torrent_hash:
            for torrent in torrents:
                if torrent.hash == entry.torrent_hash:
                    return entry, torrent

        if entry.name:
            torrent = find_torrent(entry.name, torrents)
            if torrent is not None:
                self.bind_hash(result_id, torrent.hash)
                return entry, torrent

        return entry, None
