# Funnel Magnet

AI Marketing Performance Diagnostic Agent: an ESCP MBA prototype that diagnoses changes in Meta Ads performance.

**Status:** Stage 1 complete. The diagnostic engine works and is tested, but the Streamlit app does not use it yet.

Use synthetic or anonymized data only.

## Run the app

Double-click `run.bat`, or run this from the project folder:

```powershell
& "$env:USERPROFILE\.venvs\funnel-magnet\Scripts\streamlit.exe" run app.py
```

The app opens at http://localhost:8501. Press `Ctrl+C` in the terminal to stop it.

## Run the tests

```powershell
& "$env:USERPROFILE\.venvs\funnel-magnet\Scripts\python.exe" -m pytest
```

## Set up on a new machine

The virtual environment is kept outside this folder because the folder syncs to OneDrive.

```powershell
python -m venv "$env:USERPROFILE\.venvs\funnel-magnet"
& "$env:USERPROFILE\.venvs\funnel-magnet\Scripts\python.exe" -m pip install -r requirements-dev.txt
```

## Diagnostic engine

```python
from funnel_magnet.diagnose import run_diagnosis
from funnel_magnet.periods import MONTHLY
from funnel_magnet.sources.meta import load_meta_csv

loaded = load_meta_csv("data/samples/01_cpm_inflation.csv")
diagnosis = run_diagnosis(loaded.data, MONTHLY, threshold=0.05)
```

| Module | Purpose |
|---|---|
| `funnel_magnet/sources/meta.py` | Maps and validates Meta daily CSV exports |
| `funnel_magnet/periods.py` | Builds weekly and monthly periods |
| `funnel_magnet/metrics.py` | Defines every metric from totals, using link clicks |
| `funnel_magnet/decompose.py` | Calculates exact contributions with LMDI |
| `funnel_magnet/diagnose.py` | Runs the decision tree |

A change of at least the materiality threshold is material. The default is 5%.

A metric with a zero or missing denominator is undefined. Contributions that depend on an undefined or zero value are not calculated, and the result gives the reason.

A blank cell is not treated as zero. A missing funnel value excludes that stage from any period containing it, and a blank in link clicks, purchases or purchase value blocks the file. Only if the user confirms that blanks mean 0 does `load_meta_csv(source, blank_actions_as_zero=True)` read them as 0.

Dates and number formats are read at file level. A file is accepted only when one reading fits every value, so `13/06/2026` settles the day and month order for the whole file. If two readings remain possible, or none fits, the file is rejected with a reason.

The engine reports mathematical drivers only. It does not state causes.

## Sample data

`data/samples/` holds seven invented datasets and `expected_results.json`, which declares the expected result of each one by hand. To regenerate them:

```powershell
& "$env:USERPROFILE\.venvs\funnel-magnet\Scripts\python.exe" data/samples/generate_samples.py
```
