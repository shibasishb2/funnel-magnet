"""Check the engine against the hand-declared result of each synthetic dataset."""

import csv
import dataclasses
import importlib.util
import json
from pathlib import Path

import pytest

from funnel_magnet.diagnose import run_diagnosis
from funnel_magnet.periods import MONTHLY, WEEKLY
from funnel_magnet.sources.meta import load_meta_csv

SAMPLES = Path(__file__).parent.parent / "data" / "samples"
SCENARIOS = json.loads((SAMPLES / "expected_results.json").read_text(encoding="utf-8"))
SCENARIO_IDS = [scenario["file"] for scenario in SCENARIOS]

# Words for causes that the data cannot show. The engine must never state them.
CAUSAL_WORDS = (
    "fatigue",
    "because of",
    "caused by",
    "due to",
    "bug",
    "broken",
    "saturation",
    "auction pressure",
)


def load_sample(scenario):
    result = load_meta_csv(SAMPLES / scenario["file"])
    assert result.ok, result.issues
    return result


def diagnose(scenario, granularity):
    return run_diagnosis(load_sample(scenario).data, granularity)


def test_all_seven_scenarios_are_present():
    assert len(SCENARIOS) == 7


@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_samples_load_without_issues(scenario):
    result = load_sample(scenario)

    assert result.issues == []
    assert len(result.data) == 92 * 3


@pytest.mark.parametrize("granularity", [WEEKLY, MONTHLY])
@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_scenario_reaches_expected_paths(scenario, granularity):
    result = diagnose(scenario, granularity)

    assert [list(path) for path in result.paths] == scenario["expected_paths"]
    assert result.changes["roas"].status.value == scenario["expected_roas_status"]


@pytest.mark.parametrize("granularity", [WEEKLY, MONTHLY])
@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_scenario_names_expected_driver(scenario, granularity):
    adverse = diagnose(scenario, granularity).adverse_drivers
    expected = scenario["expected_top_adverse_driver"]

    assert (adverse[0].metric if adverse else None) == expected


@pytest.mark.parametrize("granularity", [WEEKLY, MONTHLY])
@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_drivers_sum_to_the_roas_change(scenario, granularity):
    result = diagnose(scenario, granularity)
    roas = result.changes["roas"]

    assert sum(d.contribution for d in result.drivers) == pytest.approx(roas.current - roas.prior)


@pytest.mark.parametrize("granularity", [WEEKLY, MONTHLY])
def test_periods_compared(granularity):
    result = diagnose(SCENARIOS[0], granularity)
    expected = ("2026-W34", "2026-W35") if granularity == WEEKLY else ("2026-07", "2026-08")

    assert (result.prior_label, result.current_label) == expected


def test_masked_problem_shows_stable_roas_with_a_favorable_aov():
    result = diagnose(SCENARIOS[5], WEEKLY)

    assert [d.metric for d in result.favorable_drivers] == ["aov"]


@pytest.mark.parametrize("granularity", [WEEKLY, MONTHLY])
@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_engine_output_states_no_causes(scenario, granularity):
    result = diagnose(scenario, granularity)
    text = json.dumps(dataclasses.asdict(result), default=str).lower()

    assert not [word for word in CAUSAL_WORDS if word in text]
    assert "do not establish why" in text


@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_roas_matches_a_sum_of_the_raw_csv(scenario):
    with open(SAMPLES / scenario["file"], newline="", encoding="utf-8") as file:
        rows = [row for row in csv.DictReader(file) if row["Day"].startswith("2026-08")]
    revenue = sum(float(row["Purchases conversion value"]) for row in rows)
    spend = sum(float(row["Amount spent (EUR)"]) for row in rows)

    assert diagnose(scenario, MONTHLY).changes["roas"].current == pytest.approx(revenue / spend)


def test_data_needed_asks_for_evidence_rather_than_stating_a_cause():
    result = diagnose(SCENARIOS[1], WEEKLY)

    assert any("ctr" in item.lower() and "explain" in item for item in result.data_needed)


def test_generator_reproduces_the_committed_samples(tmp_path):
    spec = importlib.util.spec_from_file_location("generate_samples", SAMPLES / "generate_samples.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.generate(tmp_path)

    for scenario in SCENARIOS:
        assert (tmp_path / scenario["file"]).read_bytes() == (SAMPLES / scenario["file"]).read_bytes()
    assert (tmp_path / "expected_results.json").read_bytes() == (
        SAMPLES / "expected_results.json"
    ).read_bytes()
