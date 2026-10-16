"""Knowledge repository: bundled catalogue, overrides and failure handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iran_obd_tuner.core.exceptions import KnowledgeDatabaseError
from iran_obd_tuner.database.repository import (
    DTC_CATALOG_FILENAME,
    DtcDefinition,
    JsonKnowledgeRepository,
)


def test_bundled_catalogue_answers_known_codes(repository: JsonKnowledgeRepository) -> None:
    definition = repository.describe_dtc("p0301")

    assert isinstance(definition, DtcDefinition)
    assert definition.code == "P0301"
    assert definition.description == "Cylinder 1 misfire detected"
    assert "bundled:dtc_catalog.json" in definition.source


def test_bundled_catalogue_states_that_it_is_not_authoritative(
    repository: JsonKnowledgeRepository,
) -> None:
    metadata = repository.metadata()

    assert metadata["authoritative"] is False
    assert "review_note" in metadata
    assert "P0301" in repository.known_codes()


def test_unknown_and_malformed_codes_return_none(repository: JsonKnowledgeRepository) -> None:
    assert repository.describe_dtc("P0999") is None
    assert repository.describe_dtc("not-a-code") is None


def test_extra_directory_overrides_bundled_entries(tmp_path: Path) -> None:
    catalogue = {
        "metadata": {"name": "project-research"},
        "codes": {
            "P0301": "Overridden description (project research)",
            "P1234": "Project specific code",
        },
    }
    (tmp_path / DTC_CATALOG_FILENAME).write_text(json.dumps(catalogue), encoding="utf-8")

    repository = JsonKnowledgeRepository(tmp_path)

    assert (
        repository.describe_dtc("P0301").description == "Overridden description (project research)"
    )
    assert repository.describe_dtc("P1234").description == "Project specific code"
    # Codes that are not overridden still come from the bundled catalogue.
    assert repository.describe_dtc("P0171").description == "System too lean (bank 1)"
    assert str(tmp_path) in repository.source


def test_empty_extra_directory_is_tolerated(tmp_path: Path) -> None:
    repository = JsonKnowledgeRepository(tmp_path)

    assert repository.describe_dtc("P0301") is not None


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        "[]",
        '{"codes": {}}',
        '{"codes": {"P0301": ""}}',
        '{"codes": {"nonsense": "Some description"}}',
        '{"codes": {"P0301": "ok"}, "metadata": "not an object"}',
    ],
)
def test_malformed_catalogues_are_rejected(tmp_path: Path, payload: str) -> None:
    (tmp_path / DTC_CATALOG_FILENAME).write_text(payload, encoding="utf-8")

    with pytest.raises(KnowledgeDatabaseError):
        JsonKnowledgeRepository(tmp_path)
