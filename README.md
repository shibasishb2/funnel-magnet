# Funnel Magnet

AI Marketing Performance Diagnostic Agent: an ESCP MBA prototype that diagnoses changes in Meta Ads performance.

**Status:** Stage 3 is ready for a real API connection. The AI layer is built and tested with mocked responses, but it has not yet called the live Gemini API. Report export and deployment are not built yet.

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

## Dashboard

Choose a sample scenario or upload a Meta Ads daily export in the sidebar, then choose the period type, periods, campaign and materiality threshold. The app defaults to the latest two complete periods.

`funnel_magnet/ui/` only presents engine results and calculates no metrics. Screenshots are in `docs/screenshots/`.

## AI diagnosis

The dashboard works without an API key. **Generate AI Diagnosis** calls the model only when clicked, and the result is kept for that exact evidence. Each session allows 10 requests.

### Set up a key

1. Create a key on the API keys page in Google AI Studio, and restrict it to the Gemini API if that option is offered.
2. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, which Git ignores.
3. Replace the placeholder with your key, then restart the app.

You can set `GEMINI_API_KEY` as an environment variable instead. Never commit a key or share it in chat.

| Setting | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | None | Server-side key |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Model name |
| `GEMINI_TIER` | `free` | Set to `paid` once the Google project has billing enabled |
| `FM_SHARED_DEPLOYMENT` | `false` | Set to `true` for a shared app. AI analysis then stays off until `GEMINI_TIER` is `paid`. |

### Free and paid use

Google's Gemini API terms require paid use for an app made available to users in the EEA, Switzerland or the UK. Use the free tier for local development only. Before sharing the app with those users, enable billing on the Google project and set `GEMINI_TIER = "paid"` and `FM_SHARED_DEPLOYMENT = "true"`. These settings don't check billing, so confirm it in Google's console.

Google's free-tier terms differ from its paid terms on how data may be used, so don't send real client data through the free tier.

### What is sent and checked

`funnel_magnet/ai/evidence.py` builds the only input the model receives: calculated metrics, drivers, tree paths, data warnings and information gaps, each with a fact ID. CSV rows, campaign names and file names are never sent.

`funnel_magnet/ai/validate.py` rejects the whole response if it cites an unknown or missing fact, contains a number that isn't in the evidence, names a metric that isn't in the evidence, or presents a cause as established. Hypotheses must stay on the diagnostic path, cite their own metric and be worded as possibilities. Confidence can only be low or medium.

To use another provider, add a class with `generate()` to `funnel_magnet/ai/providers.py` and return it from `create_provider()` in `funnel_magnet/ai/service.py`.

### Limitations

- Tests use mocked responses, so the live API, its schema handling and real response quality are unverified until a key is added.
- The validator uses rules and word patterns, so it can reject a sound response or miss an unusual unsupported claim. A number that happens to match the evidence also passes.
- Rejected responses aren't retried automatically, because each attempt uses quota.
- AI output is a suggestion for a person to review. The app never changes an ad account.

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
