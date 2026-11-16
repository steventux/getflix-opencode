"""Tests for the fuzzy matching engine."""

from __future__ import annotations

import pytest

from getflix.fuzzy import (
    media_type,
    normalise,
    parse_name,
    parse_query,
    rank_hits,
    score_match,
)


def test_normalise_strips_release_noise():
    assert (
        normalise("The_Matrix.1999.1080p.BluRay.x264-GROUP")
        == "the matrix 1999 1080p bluray x264 group"
    )


@pytest.mark.parametrize(
    ("name", "season", "episode"),
    [
        ("Show.Name.S02E05.720p.WEB-DL", 2, 5),
        ("Show.Name.s2e5.720p.WEB-DL", 2, 5),
        ("Show.Name.2x05.720p.WEB-DL", 2, 5),
        ("Show.Name.Season.2.720p.WEB-DL", 2, None),
    ],
)
def test_parse_name_finds_season_and_episode(name, season, episode):
    parsed = parse_name(name)
    assert parsed.season == season
    assert parsed.episode == episode


def test_parse_name_finds_year_and_quality():
    parsed = parse_name("Dune.Part.Two.2024.2160p.WEB-DL.HDR.x265")
    assert parsed.year == 2024
    assert parsed.quality == 4


def test_parse_name_detects_tv_series():
    assert media_type("The.Office.S03E04.HDTV.x264") == "tv"
    assert media_type("Dune.2021.1080p.BluRay") == "movie"


def test_parse_name_strips_release_tags_from_the_title():
    parsed = parse_name("Dune.Part.Two.2024.1080p.WEB-DL.x264-GROUP")
    assert parsed.title == "dune part two"


def test_score_match_prefers_the_requested_year():
    query = parse_query("Dune 2021")
    good = parse_name("Dune.2021.1080p.BluRay.x264")
    bad = parse_name("Dune.2022.1080p.BluRay.x264")
    assert score_match(query, good) > score_match(query, bad)


def test_score_match_prefers_the_requested_episode():
    query = parse_query("The Office s3e4")
    good = parse_name("The.Office.S03E04.HDTV.x264")
    bad = parse_name("The.Office.S03E09.HDTV.x264")
    assert score_match(query, good) > score_match(query, bad)


def test_score_match_handles_fuzzy_titles():
    query = parse_query("dune")
    exact = parse_name("Dune.2021.1080p.BluRay.x264")
    unrelated = parse_name("Interstellar.2014.1080p.BluRay.x264")
    assert score_match(query, exact) > score_match(query, unrelated)


def test_rank_hits_only_returns_results_with_seeders():
    hits = [
        {"file_name": "Dune.2021.1080p", "seeders": 0},
        {"file_name": "Dune.2021.1080p", "seeders": 12},
    ]
    ranked = rank_hits("dune", hits)
    assert len(ranked) == 1
    assert ranked[0][1]["seeders"] == 12


def test_rank_hits_sorts_by_score_then_seeders():
    hits = [
        {"file_name": "Dune.Part.Two.2024.1080p", "seeders": 3},
        {"file_name": "Dune.2021.1080p", "seeders": 3},
        {"file_name": "Dune.2021.2160p", "seeders": 90},
    ]
    ranked = rank_hits("dune 2021", hits)
    names = [hit["file_name"] for _, hit in ranked]
    # The matching year wins, the higher resolution breaks the tie.
    assert names[0] == "Dune.2021.2160p"
    assert names[1] == "Dune.2021.1080p"
    assert names[-1] == "Dune.Part.Two.2024.1080p"


def test_rank_hits_respects_the_minimum_score():
    hits = [
        {"file_name": "Dune.2021.1080p", "seeders": 5},
        {"file_name": "Completely.Different.Movie.2019", "seeders": 5},
    ]
    assert len(rank_hits("dune", hits, min_score=60)) == 1
    assert len(rank_hits("dune", hits, min_score=0)) == 2


def test_rank_hits_can_filter_by_category():
    hits = [
        {"file_name": "Dune.2021.1080p", "seeders": 5, "category": "Movies"},
        {"file_name": "Dune.2021.1080p", "seeders": 5, "category": "TV"},
        {"file_name": "Dune.2021.1080p", "seeders": 5, "category": "Software"},
    ]
    ranked = rank_hits("dune", hits, categories=("movies",))
    assert len(ranked) == 1
    assert ranked[0][1]["category"] == "Movies"


def test_rank_hits_can_exclude_sites():
    hits = [
        {
            "file_name": "Dune.2021.1080p",
            "seeders": 5,
            "site_url": "https://www.limetorrents.lol",
        },
        {"file_name": "Dune.2021.1080p", "seeders": 5, "site_url": "https://1337x.to"},
    ]
    ranked = rank_hits("dune", hits, excluded_sites=("www.limetorrents.lol",))
    assert [hit["site_url"] for _, hit in ranked] == ["https://1337x.to"]


def test_excluded_sites_match_bare_hosts_and_subdomains_only():
    hits = [
        {
            "file_name": "Dune.2021.1080p",
            "seeders": 5,
            "site_url": "https://m.limetorrents.lol",
        },
        {
            "file_name": "Dune.2021.1080p",
            "seeders": 5,
            "site_url": "https://limetorrents.lol",
        },
        {
            "file_name": "Dune.2021.1080p",
            "seeders": 5,
            "site_url": "https://notlimetorrents.lol",
        },
    ]
    ranked = rank_hits("dune", hits, excluded_sites=("limetorrents.lol",))
    assert [hit["site_url"] for _, hit in ranked] == ["https://notlimetorrents.lol"]


def test_excluded_sites_fall_back_to_the_file_url():
    hits = [
        {
            "file_name": "Dune.2021.1080p",
            "seeders": 5,
            "file_url": "https://www.limetorrents.lol/Dune.torrent",
        }
    ]
    assert rank_hits("dune", hits, excluded_sites=("www.limetorrents.lol",)) == []


def test_rank_hits_limits_the_result_count():
    hits = [{"file_name": "Dune.2021.1080p", "seeders": 5} for _ in range(10)]
    assert len(rank_hits("dune", hits, limit=3)) == 3
