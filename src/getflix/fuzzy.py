"""Fuzzy matching of user queries against torrent release names.

Release names look like ``Movie.Title.2021.1080p.BluRay.x264-GROUP`` or
``Show.Name.S02E05.720p.WEB-DL.x265``.  To match them against a plain query
such as ``"the matrix"`` or ``"the office s3e4"`` we

1. normalise the text (separators, casing, URLs),
2. split out the parts that carry meaning (title, year, season/episode),
3. score the remainder with :mod:`rapidfuzz`.

The final score blends the textual similarity with a few soft preferences
(quality, number of seeders) so that equally good matches are ordered
sensibly.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from rapidfuzz import fuzz

__all__ = [
    "ParsedName",
    "parse_name",
    "parse_query",
    "score_match",
    "similarity",
    "rank_hits",
    "media_type",
    "normalise",
]


# ---------------------------------------------------------------------------
# normalisation helpers
# ---------------------------------------------------------------------------
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_BRACKETS_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
_SEPARATORS_RE = re.compile(r"[._]+")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")
_AUDIO_RE = re.compile(r"\b(?:ddp?|ac3|eac3|aac|truehd)[\s._-]*[57][\s._-]*1\b")

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_YEAR_INLINE_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_EPISODE_RE = re.compile(r"^s(\d{1,3})e(\d{1,3})$")
_EPISODE_MULTI_RE = re.compile(r"^s(\d{1,3})e(\d{1,3})(?:[-_]?e(\d{1,3}))?$")
_SEASON_RE = re.compile(r"^s(\d{1,3})$")
_SEASON_X_RE = re.compile(r"^(\d{1,2})x(\d{1,3})$")
_SEASON_INLINE_RE = re.compile(r"\bseason[\s._-]*(\d{1,3})\b")
_EP_INLINE_RE = re.compile(r"\bep?(\d{1,3})\b")
_EPISODE_WORD_RE = re.compile(r"\bepisode[\s._-]*(\d{1,3})\b")

#: Tags that show a name really is a release name rather than a plain title.
_RELEASE_MARKERS = (
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "xvid",
    "1080",
    "720",
    "2160",
    "480",
    "bluray",
    "webrip",
    "webdl",
    "hdtv",
    "dvdrip",
    "remux",
    "proper",
    "repack",
    "internal",
)

#: Tokens that carry no information about *what* the release is.
_NOISE_TOKENS: frozenset[str] = frozenset(
    {
        # containers / codecs
        "x264", "x265", "h264", "h265", "hevc", "avc", "xvid", "divx", "av1", "vp9",
        "mpeg2", "hdtv",
        # audio
        "aac", "aac2", "ac3", "eac3", "dts", "dtshd", "dtshdma", "truehd", "atmos",
        "flac", "mp3", "opus", "dd", "ddp", "dd5", "ddp5", "dd7", "ddp7", "lpcm",
        # source
        "bluray", "blu", "ray", "brrip", "bdrip", "bdremux", "remux", "uhdtv",
        "webrip", "web", "webdl", "dl", "hdtvrip", "pdtv", "dvdrip", "dvd", "dvdscr",
        "cam", "camrip", "ts", "tc", "r5", "screener", "scr", "hdcam", "hqcam", "telesync",
        # edition / release flags
        "proper", "repack", "internal", "limited", "extended", "unrated",
        "uncut", "remastered", "imax", "complete", "collection", "duology", "trilogy",
        "quadrilogy", "anthology", "season", "seasons", "series", "episode", "episodes",
        "volume", "vol", "pt",
        # language
        "subs", "sub", "subtitles", "subtitle", "dubbed", "multi", "dual", "english",
        "eng", "french", "fre", "fra", "german", "ger", "deu", "spanish", "spa", "ita",
        "italian", "dutch", "nld", "portuguese", "russian", "japanese", "korean",
        # streaming providers
        "nf", "amzn", "amazon", "hulu", "disney", "dsnp", "atvp", "max", "pcok",
        "hmax", "pmmp", "s01",
        # technical
        "10bit", "8bit", "hi10p", "hdr", "hdr10", "dv", "dolby", "vision", "sdr",
        "retail", "ntsc", "pal",
        # tracker noise
        "www", "site", "com", "net", "org", "torrent", "rls", "nzb", "magnet",
        "omg", "eztv", "rarbg", "yify", "yts", "ntb", "ettv", "tig", "cmg", "af", "nt",
    }
)

#: Quality tags mapped to a rough ranking (higher is better).
_QUALITY_TOKENS: dict[str, int] = {
    "2160p": 4,
    "4k": 4,
    "uhd": 4,
    "1080p": 3,
    "1080i": 3,
    "fhd": 3,
    "720p": 2,
    "480p": 1,
    "360p": 0,
    "sd": 0,
}

#: Words that hint at a TV series even without an explicit SxxEyy marker.
_TV_HINTS = {"series", "season", "seasons", "episode", "episodes", "collection"}


def normalise(text: str) -> str:
    """Lowercase ``text`` and reduce it to comparable alphanumeric tokens."""
    if not text:
        return ""
    cleaned = _URL_RE.sub(" ", text)
    cleaned = cleaned.lower().replace("&", " and ")
    cleaned = _BRACKETS_RE.sub(" ", cleaned)
    cleaned = _AUDIO_RE.sub(" ", cleaned)
    cleaned = _SEPARATORS_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("-", " ").replace("+", " ")
    cleaned = _NON_WORD_RE.sub(" ", cleaned)
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def _strip_release_group(raw: str) -> str:
    """Remove the trailing ``-GROUP`` suffix from a release name.

    Only the segment after the very last dash is considered, and only when the
    rest of the name clearly is a release name.  That keeps titles such as
    ``Spider-Man`` intact.
    """
    if "-" not in raw:
        return raw
    prefix, _, group = raw.rpartition("-")
    if not prefix or not group:
        return raw
    group = group.strip()
    if not group or len(group) > 20 or " " in group:
        return raw
    lowered_prefix = prefix.lower()
    if not any(marker in lowered_prefix for marker in _RELEASE_MARKERS):
        return raw
    return prefix


@dataclass(frozen=True)
class ParsedName:
    """A release name (or a user query) broken into its meaningful parts."""

    raw: str
    normalized: str
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    quality: int = 0
    kind: str = "unknown"  # "movie" | "tv" | "unknown"

    @property
    def tokens(self) -> tuple[str, ...]:
        return tuple(self.title.split())


def _extract_season_episode(
    tokens: Sequence[str], raw_normalized: str
) -> tuple[int | None, int | None]:
    season: int | None = None
    episode: int | None = None

    for token in tokens:
        match = _EPISODE_MULTI_RE.match(token)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
            break
        match = _EPISODE_RE.match(token)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
            break
        match = _SEASON_X_RE.match(token)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
            break
        match = _SEASON_RE.match(token)
        if match and season is None:
            season = int(match.group(1))
            continue

    if season is None:
        inline = _SEASON_INLINE_RE.search(raw_normalized)
        if inline:
            season = int(inline.group(1))

    if episode is None:
        inline = _EPISODE_WORD_RE.search(raw_normalized)
        if inline:
            episode = int(inline.group(1))
        else:
            inline = _EP_INLINE_RE.search(raw_normalized)
            if inline:
                episode = int(inline.group(1))

    return season, episode


def parse_name(raw: str) -> ParsedName:
    """Parse a release name into title / year / season / episode / quality."""
    raw = _strip_release_group(raw.strip())
    normalized = normalise(raw)
    tokens = normalized.split()

    season, episode = _extract_season_episode(tokens, normalized)

    year: int | None = None
    for token in tokens:
        if _YEAR_RE.match(token):
            year = int(token)
            break
    if year is None:
        inline = _YEAR_INLINE_RE.search(normalized)
        if inline:
            year = int(inline.group(1))

    quality = 0
    for token in tokens:
        quality = max(quality, _QUALITY_TOKENS.get(token, 0))

    kept: list[str] = []
    for token in tokens:
        if token in _NOISE_TOKENS:
            continue
        if token in _QUALITY_TOKENS:
            continue
        if _YEAR_RE.match(token):
            continue
        if _EPISODE_RE.match(token) or _EPISODE_MULTI_RE.match(token):
            continue
        if _SEASON_RE.match(token) or _SEASON_X_RE.match(token):
            continue
        kept.append(token)
    title = " ".join(kept).strip()

    if season is not None or episode is not None or any(token in _TV_HINTS for token in tokens):
        kind = "tv"
    elif year is not None:
        kind = "movie"
    else:
        kind = "unknown"

    return ParsedName(
        raw=raw,
        normalized=normalized,
        title=title,
        year=year,
        season=season,
        episode=episode,
        quality=quality,
        kind=kind,
    )


def parse_query(query: str) -> ParsedName:
    """Parse the user's search query."""
    return parse_name(query)


def media_type(name: str) -> str:
    """``"movie"``, ``"tv"`` or ``"unknown"`` for a release name."""
    return parse_name(name).kind


#: Very common words that carry no identifying information.  They are removed
#: before two names are compared, otherwise "the" alone would make unrelated
#: titles look like a match.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "of", "and", "or", "to", "in", "on", "at", "for", "with",
        "from", "by", "as", "is", "it", "its", "my", "our", "your", "no", "vs",
        "us", "uk", "au", "part2", "vol2",
    }
)


def _meaningful_tokens(title: str) -> list[str]:
    """The tokens of ``title`` that are not stop words."""
    return [token for token in title.split() if token not in _STOP_WORDS]


def similarity(query: ParsedName, candidate: ParsedName) -> float:
    """How well the two names match textually, from 0 to 100.

    ``token_set_ratio`` is used because it copes well with the extra tokens a
    release name carries: searching for ``dune`` matches
    ``Dune.Part.Two.2024.1080p.WEB-DL`` perfectly.
    """
    if query.title and candidate.title:
        left_tokens = _meaningful_tokens(query.title)
        right_tokens = _meaningful_tokens(candidate.title)
        if left_tokens and right_tokens:
            left = " ".join(left_tokens)
            right = " ".join(right_tokens)
        else:
            left = query.title
            right = candidate.title
        return float(fuzz.token_set_ratio(left, right))
    if query.normalized and candidate.normalized:
        return float(fuzz.token_set_ratio(query.normalized, candidate.normalized))
    return 0.0


def score_match(query: ParsedName, candidate: ParsedName, seeders: int = 0) -> float:
    """Score how well ``candidate`` matches ``query``.

    The textual similarity dominates; the year, episode, quality and seeder
    counts are used to break ties between equally similar releases.
    """
    score = similarity(query, candidate)

    # Year agreement is a strong signal.
    if query.year is not None:
        if candidate.year is None:
            score -= 4
        elif candidate.year == query.year:
            score += 8
        else:
            score -= 12

    # Episode / season agreement matters a lot for TV.
    if query.season is not None:
        if candidate.season == query.season:
            score += 10
        elif candidate.season is not None:
            score -= 25
        else:
            score -= 8
    if query.episode is not None:
        if candidate.episode == query.episode:
            score += 12
        elif candidate.episode is not None:
            score -= 30

    # Soft preferences, only used to break ties between similar matches.  They
    # are capped so that they can never rescue an unrelated release.
    bonus = candidate.quality * 1.2
    if seeders > 0:
        bonus += min(5.0, math.log10(seeders + 1) * 2.0)
    score += min(9.8, bonus)

    return score


def _hits_attr(hit: Any, name: str, default: Any = None) -> Any:
    if isinstance(hit, dict):
        return hit.get(name, default)
    return getattr(hit, name, default)


def _site_host(value: str) -> str:
    """The lower case host of a URL or bare host name, without a leading www."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "//" not in text:
        text = f"//{text}"
    host = urlparse(text).hostname or ""
    return host.removeprefix("www.")


def _is_excluded_site(hit: Any, blocked: set[str]) -> bool:
    """Whether ``hit`` comes from one of the blocked hosts (or a subdomain)."""
    hosts = {
        _site_host(str(_hits_attr(hit, "site_url", "") or "")),
        _site_host(str(_hits_attr(hit, "details_url", "") or "")),
        _site_host(str(_hits_attr(hit, "file_url", "") or "")),
    }
    hosts.discard("")
    return any(
        host == site or host.endswith(f".{site}") for host in hosts for site in blocked
    )


def rank_hits(
    query: str,
    hits: Iterable[Any],
    *,
    min_score: float = 0.0,
    limit: int | None = None,
    categories: Sequence[str] = (),
    excluded_sites: Sequence[str] = (),
) -> list[tuple[float, Any]]:
    """Filter and rank search hits.

    Only results with at least one seeder are returned.  ``categories`` is an
    optional list of qBittorrent search categories to keep; results without a
    category are always kept because most plugins do not report one.
    ``excluded_sites`` lists host names (for example ``limetorrents.lol``)
    whose results are dropped, including their subdomains.
    """
    parsed_query = parse_query(query)
    allowed = {c.lower() for c in categories if c}
    blocked = {_site_host(site) for site in excluded_sites if str(site or "").strip()}
    ranked: list[tuple[float, Any]] = []

    for hit in hits:
        if blocked and _is_excluded_site(hit, blocked):
            continue

        seeders = int(_hits_attr(hit, "seeders", 0) or 0)
        if seeders <= 0:
            continue

        category = str(_hits_attr(hit, "category", "") or "").lower()
        if allowed and category and not any(cat in category for cat in allowed):
            continue

        name = str(_hits_attr(hit, "file_name", "") or _hits_attr(hit, "name", "") or "")
        parsed = parse_name(name)
        score = score_match(parsed_query, parsed, seeders)
        if score < min_score:
            continue
        ranked.append((score, hit))
    ranked.sort(
        key=lambda item: (
            -item[0],
            -int(_hits_attr(item[1], "seeders", 0) or 0),
            str(_hits_attr(item[1], "file_name", "") or "").lower(),
        )
    )
    if limit is not None and limit > 0:
        ranked = ranked[:limit]
    return ranked
