"""Search snippets highlight whole words, not fragments inside other words."""

from __future__ import annotations

from llamora.app.services.lexical_reranker import LexicalReranker


def entry(content: str, entry_id: str = "e1", cosine: float = 0.8) -> dict:
    return {
        "id": entry_id,
        "content": content,
        "created_at": "2026-09-27T12:00:00",
        "created_date": "2026-09-27",
        "role": "user",
        "cosine": cosine,
    }


def search(query: str, *contents: str) -> list[dict]:
    candidates = [entry(c, f"e{i}") for i, c in enumerate(contents)]
    return LexicalReranker().rerank(query, candidates, limit=10)


def hits(result: dict) -> list[str]:
    return [s["text"] for s in result["snippet"]["segments"] if s["hit"]]


def test_a_word_is_not_found_inside_another_word() -> None:
    (result,) = search("light", "The twilight was violet.")
    assert hits(result) == []
    assert result["status"] == "semantic"


def test_a_longer_word_still_matches_its_own_forms() -> None:
    (result,) = search("light walk", "Lights on, walking home in the light.")
    assert hits(result) == ["Light", "walk", "light"]


def test_short_words_only_match_whole_words() -> None:
    (result,) = search("ice", "Practice, then ice on the lake.")
    assert hits(result) == ["ice"]


def test_a_long_query_does_not_light_up_fragments() -> None:
    # "at", "in", "the" and "is" used to match inside "that", "thinking",
    # "there" and "this"; now only whole content words are highlighted.
    (result,) = search(
        "the wind at the window in the night",
        "I kept thinking that there is wind, and this window rattles all night.",
    )
    assert hits(result) == ["wind", "window", "night"]


def test_common_words_do_not_count_towards_the_ranking() -> None:
    near, far = search(
        "walking in the rain",
        "There is a thing in the attic.",
        "Walking home through the rain.",
    )
    assert near["id"] == "e1" and near["status"] == "token"
    assert far["status"] == "semantic"


def test_a_query_of_only_common_words_still_matches_them() -> None:
    (result,) = search("to be", "Something to be glad about.")
    assert hits(result) == ["to be"]
    assert result["status"] == "exact"


def test_the_exact_phrase_is_marked_as_one_hit() -> None:
    (result,) = search("golden light", "The golden light on the desk.")
    assert [s["kind"] for s in result["snippet"]["segments"] if s["hit"]] == ["exact"]
    assert hits(result) == ["golden light"]


def test_the_exact_phrase_must_start_a_word_too() -> None:
    # "old light" is not inside "golden light"; "light" alone still matches.
    (result,) = search("old light", "A golden light.")
    assert hits(result) == ["light"]
    assert result["status"] == "token"


# -- ranking ------------------------------------------------------------------


def rank(query: str, *entries: dict, boosts: dict[str, float] | None = None) -> list:
    return LexicalReranker().rerank(query, list(entries), limit=10, tag_boosts=boosts)


def test_exact_phrase_beats_words_beats_traces_beats_meaning() -> None:
    results = rank(
        "golden light",
        entry("Nothing in common, but close in meaning.", "meaning", cosine=0.95),
        entry("Traced, not written.", "traced", cosine=0.1),
        entry("Light, and something golden.", "words", cosine=0.3),
        entry("The golden light on the desk.", "phrase", cosine=0.2),
        boosts={"traced": 1.0},
    )
    assert [r["id"] for r in results] == ["phrase", "words", "traced", "meaning"]
    assert [r["status"] for r in results] == ["exact", "token", "tag", "semantic"]


def test_more_of_the_query_ranks_higher() -> None:
    results = rank(
        "river stones evening",
        entry("Stones by the river in the evening.", "all", cosine=0.1),
        entry("A river.", "one", cosine=0.9),
    )
    assert [r["id"] for r in results] == ["all", "one"]


def test_a_trace_adds_to_matching_words() -> None:
    results = rank(
        "river",
        entry("The river again.", "plain", cosine=0.9),
        entry("The river, traced.", "traced", cosine=0.1),
        boosts={"traced": 1.0},
    )
    assert [r["id"] for r in results] == ["traced", "plain"]


def test_meaning_decides_among_the_rest() -> None:
    results = rank(
        "zzz",
        entry("far", "far", cosine=0.2),
        entry("near", "near", cosine=0.8),
    )
    assert [r["id"] for r in results] == ["near", "far"]


def test_weak_meaning_matches_are_marked_poor() -> None:
    (weak,) = rank("zzz", entry("unrelated", cosine=0.05))
    (strong,) = rank("zzz", entry("related", cosine=0.9))
    assert "status-poor" in weak["css_class"]
    assert "status-poor" not in strong["css_class"]


def test_limit_and_empty_candidates() -> None:
    reranker = LexicalReranker()
    many = [entry(f"entry {i}", f"e{i}") for i in range(5)]
    assert len(reranker.rerank("entry", many, limit=2)) == 2
    assert reranker.rerank("entry", [], limit=5) == []


# -- snippets -----------------------------------------------------------------


def text(result: dict) -> str:
    return "".join(s["text"] for s in result["snippet"]["segments"])


def test_a_short_entry_is_shown_whole() -> None:
    (result,) = search("heron", "A heron stood still.")
    assert text(result) == "A heron stood still."
    snippet = result["snippet"]
    assert not snippet["leading_ellipsis"] and not snippet["trailing_ellipsis"]


def test_a_long_entry_is_cut_around_the_first_match_on_word_boundaries() -> None:
    before = "word " * 60
    after = " more" * 200
    (result,) = search("heron", f"{before}the heron{after}")
    shown = text(result)
    snippet = result["snippet"]

    assert snippet["leading_ellipsis"] and snippet["trailing_ellipsis"]
    assert "heron" in hits(result)
    assert len(shown) <= 500
    # Cut between words, never through one.
    assert shown.startswith(("word", "the")) and shown.rstrip().endswith("more")


def test_without_a_match_the_snippet_starts_at_the_beginning() -> None:
    (result,) = search("zzz", "Start of the entry. " * 40)
    assert text(result).startswith("Start of the entry.")
    assert not result["snippet"]["leading_ellipsis"]
    assert result["snippet"]["trailing_ellipsis"]


def test_accents_and_emoji_survive() -> None:
    (result,) = search("café", "Morning at the café ☕, then rain 🌧.")
    assert text(result) == "Morning at the café ☕, then rain 🌧."
    assert hits(result) == ["café"]


def test_matching_ignores_case_but_shows_the_original() -> None:
    (result,) = search("HERON", "A Heron, then another heron.")
    assert hits(result) == ["Heron", "heron"]
