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
