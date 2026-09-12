from pathlib import Path

import pytest

from app.services.education_content_service import (
    EducationContentError,
    load_player_sample_manifest,
    normalize_bcp47_locale,
)


def test_bcp47_locale_is_data_driven_and_canonicalized():
    assert normalize_bcp47_locale("en") == "en"
    assert normalize_bcp47_locale("HU") == "hu"
    assert normalize_bcp47_locale("pt-br") == "pt-BR"
    assert normalize_bcp47_locale("zh-Hant-TW") == "zh-Hant-TW"


@pytest.mark.parametrize("value", ["", "english", "en_US", "x", "hu--HU"])
def test_invalid_locale_fails_closed(value):
    with pytest.raises(EducationContentError, match="INVALID_LOCALE"):
        normalize_bcp47_locale(value)


def test_sample_manifest_is_player_only_draft_and_maps_every_corpus_file():
    manifest_path = Path("content/education/lfa_football_player/pc3_sample_manifest.json")
    manifest = load_player_sample_manifest(manifest_path)

    mapped = {
        variant["source_path"]
        for module in manifest["modules"]
        for lesson in module["lessons"]
        for assessment in lesson.get("assessments", [])
        for variant in assessment["variants"]
    }
    corpus = {
        str(path)
        for root in (
            Path("content/adaptive_learning/lfa_football_player"),
            Path("content/adaptive_learning/_shared/sports_physiology"),
        )
        for path in root.rglob("*.json")
    }

    assert manifest["program_id"] == "LFA_FOOTBALL_PLAYER"
    assert manifest["release"]["status"] == "DRAFT"
    assert len(mapped) == 31
    assert len(manifest["modules"]) == 7
    assert sum(len(module["lessons"]) for module in manifest["modules"]) == 21
    assert sum(
        len(lesson.get("assessments", []))
        for module in manifest["modules"]
        for lesson in module["lessons"]
    ) == 23
    assert mapped == corpus
