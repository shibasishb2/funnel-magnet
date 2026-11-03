# Funnel Magnet

AI Marketing Performance Diagnostic Agent: an ESCP MBA prototype that diagnoses changes in Meta Ads performance.

**Status:** Stages 0 to 4 are complete, and the project is prepared for Streamlit Community Cloud. Python writes the mathematical diagnosis, and Gemini only interprets it. AI analysis is off by default. No live Gemini response has passed every check, so the app shows the sections that pass and flags the rest.

Use synthetic or anonymized data only.

Results are decision support for a person to review. They are not automatically verified causal conclusions.

## Academic prototype and future product

This repository is the academic prototype, built for an ESCP MBA assignment. A commercial Funnel Magnet product does not exist yet. The right column lists possible directions, not commitments.

| | Academic prototype (this repository) | Possible future commercial product |
|---|---|---|
| Purpose | Show the diagnostic method | Support paying clients' marketing decisions |
| Data | Synthetic or anonymized Meta Ads CSV files | Client data, under suitable contracts and privacy terms |
| Connections | Manual CSV upload | Possible Meta API and other ad platforms |
| AI | Off by default, and validated with rules | Paid-tier use with stronger safeguards and evaluation |
| Access | Open link with no accounts | Accounts, subscriptions and payments |
| Reports | Downloadable HTML | To be decided |
| Support | None, and no service guarantee | To be decided |

## Run the app locally

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

This needs Python 3.12. Keep the virtual environment outside any cloud-synced folder.

```powershell
python -m venv "$env:USERPROFILE\.venvs\funnel-magnet"
& "$env:USERPROFILE\.venvs\funnel-magnet\Scripts\python.exe" -m pip install -r requirements-dev.txt
```

## Deploy to Streamlit Community Cloud

`requirements.txt` holds only what the app needs at run time. Test tools are in `requirements-dev.txt`.

1. Push this repository to GitHub.
2. In Streamlit Community Cloud, create an app from the repository, on branch `main`, with `app.py` as the main file.
3. Under Advanced settings, choose Python 3.12.
4. Leave Secrets empty, then deploy.

With no secrets, the app stays fully usable for sample scenarios, CSV upload, the dashboard and report downloads, and it cannot call Gemini. Never add a key to a file in the repository.

Uploads are limited to 20 MB. Community Cloud apps can sleep when unused, so open the link shortly before a demonstration.

To switch AI on later, first enable billing on the Google project and confirm it in Google's console. Then add these under the app's Secrets settings:

```toml
FM_AI_ENABLED = "true"
GEMINI_API_KEY = "your-key"
GEMINI_TIER = "paid"
FM_SHARED_DEPLOYMENT = "true"
```

The only spending safeguard in the app is the limit of 10 requests per browser session. It doesn't limit total use across visitors, so set a budget alert in Google Cloud and add stronger limits before enabling AI publicly.

## Dashboard

Choose a sample scenario or upload a Meta Ads daily export in the sidebar, then choose the period type, periods, campaign and materiality threshold. The app defaults to the latest two complete periods.

`funnel_magnet/ui/` only presents engine results and calculates no metrics. Screenshots in `docs/screenshots/` come from earlier stages, so the current layout differs slightly.

## AI diagnosis

The dashboard works without AI. AI analysis stays off until `FM_AI_ENABLED` is `true`, even if a key is present. **Generate AI Diagnosis** calls the model only when clicked, and the result is kept for that exact evidence. Each session allows 10 requests.

### Set up a key

1. Create a key on the API keys page in Google AI Studio, and restrict it to the Gemini API if that option is offered.
2. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, which Git ignores.
3. Replace the placeholder with your key, keep `FM_AI_ENABLED = "true"`, then restart the app.

You can set these as environment variables instead. Never commit a key or share it in chat.

| Setting | Default | Purpose |
|---|---|---|
| `FM_AI_ENABLED` | `false` | Must be `true` before any live AI call is possible |
| `GEMINI_API_KEY` | None | Server-side key |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Model name |
| `GEMINI_TIER` | `free` | Set to `paid` once the Google project has billing enabled |
| `FM_SHARED_DEPLOYMENT` | `false` | Set to `true` for a shared app. AI analysis then stays off until `GEMINI_TIER` is `paid`. |

### Free and paid use

Google's Gemini API terms require paid use for an app made available to users in the EEA, Switzerland or the UK. Use the free tier for local development only. Before sharing the app with those users, enable billing on the Google project and set `GEMINI_TIER = "paid"` and `FM_SHARED_DEPLOYMENT = "true"`. These settings don't check billing, so confirm it in Google's console.

Google's free-tier terms differ from its paid terms on how data may be used, so don't send real client data through the free tier.

### What is sent and checked

Python writes the mathematical diagnosis from the decision tree and contributions, and it appears with or without a key. Gemini receives that diagnosis and writes only hypotheses, recommendations, uncertainty explanations and missing-data requirements.

`funnel_magnet/ai/evidence.py` builds the only input the model receives: the mathematical diagnosis, calculated metrics, drivers, tree paths, data warnings, information gaps and the checks available in the uploaded data, with fact IDs on every fact. CSV rows, campaign names and file names are never sent.

`funnel_magnet/ai/validate.py` checks each section of the response. A section fails if it cites an unknown or missing fact, contains a number that isn't in the evidence, names a metric that isn't in the evidence, or presents a cause as established. Hypotheses must stay on the diagnostic path, keep mathematical evidence apart from the unverified business cause, and hold one business cause each, so "X or Y" fails. Statements that a cause is unknown, or that evidence is missing, are allowed, but campaign-level data can't be called missing when the upload has it. "If supported" and "if not supported" steps may name a possible cause, because they only apply if the check holds, and they must still pass the number, proof and definite-cause checks. Each wording problem is reported once per sentence.

A section that fails is withheld, and its text is never shown. The panel is marked "Partly verified" and lists each reason. A recommendation that passes stays visible even if its hypothesis was withheld, and it is labelled as such. If no hypothesis or recommendation passes, the whole response is rejected.

`funnel_magnet/ai/normalize.py` runs first and fixes harmless differences, listing each change under "Adjusted by the app":

- Keeps the top three hypotheses, and renumbers hypotheses and recommendations.
- Adds a hypothesis's own metric to its citations, and marks an unhedged business cause as possible.
- Lowers confidence to low unless a hypothesis cites a separate lowest-level metric that moved materially, and forces low when purchase volume is low.
- Relabels a check as external if it needs outside data, and moves checks using the uploaded data first.
- Adds any outside data named in a recommendation, including benchmark and industry data, to the additional information needed. Next steps may lead to external checks, because that data is then disclosed.

To use another provider, add a class with `generate()` to `funnel_magnet/ai/providers.py` and return it from `create_provider()` in `funnel_magnet/ai/service.py`.

### Limitations

- Automated tests use mocked responses. Response quality and acceptance rates on the live API still need testing.
- The validator uses rules and word patterns, so it can reject a sound response or miss an unusual unsupported claim. A number that happens to match the evidence also passes.
- The check for two causes in one hypothesis looks for the word "or", so causes joined by "and" can pass, and one cause worded with "or" is withheld. Whether actions are concrete is requested in the prompt, not enforced.
- Rejected responses aren't retried automatically, because each attempt uses quota.
- AI output is a suggestion for a person to review. The app never changes an ad account.

## Diagnostic report

**Download Diagnostic Report** on the dashboard saves one HTML file for the current comparison. It opens in any modern browser with no server, and it has no scripts, outside stylesheets or outside requests. Use the browser's print command to print it.

`funnel_magnet/ui/report.py` builds the report from engine results and the AI result, and it calculates no metrics. All text from uploads and the model is escaped. Keys and settings are never passed to it.

- **No AI result:** the report is complete and factual, and says it contains no AI interpretation.
- **Partly verified AI result:** a notice at the top discloses that content was withheld, only accepted sections appear, and a recommendation whose hypothesis was withheld is labelled.

The report has no charts, so contributions appear as a table. The analysis date is the server's date.

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

The tree follows every material change, whether it raised or lowered ROAS. It also looks beneath a stable metric whose inputs moved materially, so a CPM rise offset by a CTR rise still appears even when CPC barely moved. Each driver is labelled by its effect: a positive or adverse contributor moves with the ROAS change, and an offsetting driver works against it. The main driver is the largest contribution moving with ROAS.

A metric with a zero or missing denominator is undefined. Contributions that depend on an undefined or zero value are not calculated, and the result gives the reason.

A blank cell is not treated as zero. A missing funnel value excludes that stage from any period containing it, and a blank in link clicks, purchases or purchase value blocks the file. Only if the user confirms that blanks mean 0 does `load_meta_csv(source, blank_actions_as_zero=True)` read them as 0.

An account-level export with no campaign column is analysed as one total labelled "All campaigns", with a warning, and no campaign breakdown is offered. When both "Website landing page views" and "Landing page views" are exported, the funnel uses the website column, and any row where the two differ is reported as a warning.

Dates and number formats are read at file level. A file is accepted only when one reading fits every value, so `13/06/2026` settles the day and month order for the whole file. If two readings remain possible, or none fits, the file is rejected with a reason.

The engine reports mathematical drivers only. It does not state causes.

## Sample data

`data/samples/` holds seven invented datasets and `expected_results.json`, which declares the expected result of each one by hand. To regenerate them:

```powershell
& "$env:USERPROFILE\.venvs\funnel-magnet\Scripts\python.exe" data/samples/generate_samples.py
```
