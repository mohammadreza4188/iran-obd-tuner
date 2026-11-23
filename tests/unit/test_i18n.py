"""Translation completeness: both languages, all keys, matching placeholders, no duplicates.

Runs without Qt: ``ui/i18n.py`` imports nothing from PySide6, and the keys used by the window
are collected from the source text of ``ui/main_window.py`` and ``ui/safety_modal.py``.
"""

from __future__ import annotations

import ast
import re
import string
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from iran_obd_tuner.ui import i18n
from iran_obd_tuner.ui.i18n import COPY, LANGUAGES, format_message

UI_DIR = Path(i18n.__file__).parent
WINDOW_SOURCE = (UI_DIR / "main_window.py").read_text(encoding="utf-8")
MODAL_SOURCE = (UI_DIR / "safety_modal.py").read_text(encoding="utf-8")
VIEW_SOURCES = "".join(
    (UI_DIR / name).read_text(encoding="utf-8")
    for name in (
        "diagnostics_view.py",
        "traffic_view.py",
        "performance_view.py",
        "uds_view.py",
        "knowledge_view.py",
        "bench_view.py",
        "bench_flash_panel.py",
    )
)


def _flatten(tree: Mapping[str, Any], prefix: str = "") -> Iterator[tuple[str, str]]:
    for key, value in tree.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            yield from _flatten(value, f"{path}/")
        else:
            yield path, value


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def test_both_languages_have_the_same_keys() -> None:
    fa = dict(_flatten(COPY["fa"]))
    en = dict(_flatten(COPY["en"]))

    assert set(fa) == set(en)
    assert set(COPY) == set(LANGUAGES)


def test_placeholders_match_between_languages() -> None:
    fa = dict(_flatten(COPY["fa"]))
    en = dict(_flatten(COPY["en"]))

    mismatched = [key for key in fa if _placeholders(fa[key]) != _placeholders(en[key])]
    assert mismatched == []


def test_no_string_is_empty() -> None:
    for lang in LANGUAGES:
        empty = [key for key, value in _flatten(COPY[lang]) if not str(value).strip()]
        assert empty == [], lang


def test_no_key_is_written_twice() -> None:
    """A repeated key in a dict literal silently overwrites the first value."""
    tree = ast.parse((UI_DIR / "i18n.py").read_text(encoding="utf-8"))
    duplicates = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            duplicates += [key for key in set(keys) if keys.count(key) > 1]
    assert duplicates == []


def test_persian_strings_are_persian() -> None:
    """Regression: duplicated keys once made Persian tabs show English text."""
    persian = re.compile(r"[؀-ۿ]")
    for key in (
        "bench_title",
        "bench_warning",
        "ident_btn",
        "safety_heading",
        "sim_banner",
        "bflash_title",
        "bflash_intro",
        "bflash_do_not_disconnect",
        "bflash_banner_dirty",
    ):
        assert persian.search(COPY["fa"][key]), key


def test_every_top_level_key_used_by_the_window_exists() -> None:
    used = set(re.findall(r'\bt\["(\w+)"\]', WINDOW_SOURCE + MODAL_SOURCE + VIEW_SOURCES))
    used |= set(re.findall(r'COPY\[self\.lang\]\["(\w+)"\]', WINDOW_SOURCE))
    # identification errors are stored as (key, detail) and rendered later with t[key]
    used |= set(re.findall(r'\("(ident_\w+)", ', WINDOW_SOURCE))
    # the Bench view selects its status line by key: self._say("bench_...")
    bench_status = set(re.findall(r'_say\(\s*"(\w+)"', VIEW_SOURCES))
    assert len(bench_status) >= 8, bench_status
    used |= bench_status
    # export results are picked by key in the knowledge view
    used |= set(re.findall(r'"(kb_export_\w+)"', VIEW_SOURCES))

    assert used, "the regular expressions found nothing; update this test"
    for lang in LANGUAGES:
        missing = sorted(key for key in used if key not in COPY[lang])
        assert missing == [], lang


def test_every_log_message_used_by_the_window_exists() -> None:
    used = set(re.findall(r'_append_log\(\s*"(msg\.\w+)"', WINDOW_SOURCE))

    assert len(used) >= 10
    for lang in LANGUAGES:
        missing = sorted(key for key in used if key not in COPY[lang]["messages"])
        assert missing == [], lang


def test_gauge_tab_and_status_keys_exist() -> None:
    gauge_keys = re.findall(r'GaugeSpec\("(\w+)", "\w+", "(\w+)"', WINDOW_SOURCE)
    tab_ids = re.search(r"self\.tab_ids = \[(.*?)\]", WINDOW_SOURCE, re.DOTALL)
    status_keys = re.findall(r'PidStatus\.\w+: "(\w+)"', WINDOW_SOURCE)

    assert len(gauge_keys) == 8
    assert tab_ids is not None
    assert len(status_keys) == 3
    for lang in LANGUAGES:
        t = COPY[lang]
        for gauge, unit in gauge_keys:
            assert gauge in t["gauges"]
            assert unit in t["units"]
        for tab in re.findall(r'"(\w+)"', tab_ids.group(1)):
            assert tab in t["tabs"]
        for status in [*status_keys, "no_data"]:
            assert status in t["pid_status"]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_format_message_substitutes_arguments(lang: str) -> None:
    text = format_message(lang, "msg.recording_stopped", 12, "capture.csv")  # type: ignore[arg-type]

    assert "12" in text
    assert "capture.csv" in text


def test_format_message_returns_unknown_keys_unchanged() -> None:
    assert format_message("en", "msg.does_not_exist") == "msg.does_not_exist"


def test_nested_m3_tables_are_complete() -> None:
    from iran_obd_tuner.core.dtc import DtcStatus
    from iran_obd_tuner.diagnostics.models import PidStatus, ReadOutcome
    from iran_obd_tuner.diagnostics.service import FREEZE_FRAME_PIDS

    for lang in LANGUAGES:
        t = COPY[lang]
        assert set(t["dtc_category"]) == {s.value for s in DtcStatus}
        assert {o.value for o in ReadOutcome} <= set(t["outcome"])
        assert {"ok", *(s.value for s in PidStatus if s is not PidStatus.OK)} <= set(
            t["pid_status"]
        )
        assert set(FREEZE_FRAME_PIDS) <= set(t["ff_params"])
        assert set(t["mil"]) == {"disconnected", "on", "off", "unknown"}


def test_bench_flash_tables_are_complete() -> None:
    """Milestone 7f: every journal state, stage, check and attestation item is translated."""
    from iran_obd_tuner.core.bench import BenchAttestation, FlashStage, JournalState
    from iran_obd_tuner.core.bench_reports import BenchCheckKey, BenchJournalStatus

    for lang in LANGUAGES:
        t = COPY[lang]
        assert set(t["bflash_states"]) == {s.value for s in JournalState}
        assert set(t["bflash_steps"]) == {s.value for s in FlashStage} - {"recovery"}
        assert set(t["bflash_checks"]) == {k.value for k in BenchCheckKey}
        assert set(t["bflash_journal_status"]) == {s.value for s in BenchJournalStatus}
        assert set(t["bflash_statements"]) == set(BenchAttestation.STATEMENTS)
        assert set(t["bflash_unmet"]) == {
            *BenchAttestation.STATEMENTS,
            "operator",
            "confirmation_phrase",
            "machine_checks",
        }
        for table in ("bflash_modal_title", "bflash_modal_intro", "bflash_confirm"):
            assert set(t[table]) == {"flash", "recovery"}, table
        assert "{0}" in t["bflash_phrase"]  # the fixed phrase is shown, never translated
