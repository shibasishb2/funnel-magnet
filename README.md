# Funnel Magnet

AI Marketing Performance Diagnostic Agent: an ESCP MBA prototype that diagnoses changes in Meta Ads performance.

**Status:** Stages 0 to 4 are complete, and the app runs as a public demonstration on Streamlit Community Cloud. Python finds the problem KPIs, and a marketing playbook supplies possible causes, checks and conditional actions. An optional AI step only orders and explains them, and AI is off by default.

Use synthetic or anonymized data only. The upload panel says so before any file is chosen, and AI prioritisation sends Groq only totals and playbook text, never rows, campaign names or file names. Groq stores data in the United States, so client or personal data must not be uploaded to the demonstration.

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
4. Add the secrets below, then deploy.

Uploads are limited to 20 MB. Community Cloud apps can sleep when unused, so open the link shortly before a demonstration.

### Secrets for the public demonstration

In Streamlit Community Cloud, open the app's **Settings**, then **Secrets**, and paste this with your own key:

```toml
FM_SHARED_DEPLOYMENT = "true"
FM_AI_ENABLED = "true"
AI_PROVIDER = "groq"
GROQ_API_KEY = "your Groq key"
GROQ_MODEL = "openai/gpt-oss-120b"
```

Save, and the app restarts with them. Never add a key to a file in the repository. The app is public, with no sign-in: anyone with the link can upload a file, see the diagnosis and use the shared AI allowance below. Without the AI settings, the full diagnosis and report still work.

Use a Groq key from the free plan. The app has no setting that can buy paid usage, and the limits below are not a billing cap, so don't attach a payment method to the Groq organisation used for the demonstration.

## Dashboard

Choose a sample scenario or upload a Meta Ads daily export in the sidebar, then choose the period type, periods, campaign and materiality threshold. The app defaults to the latest two complete periods.

`funnel_magnet/ui/` only presents engine results and calculates no metrics. Screenshots in `docs/screenshots/` come from earlier stages, so the current layout differs slightly.

## Marketing diagnosis

The page opens with a short **business summary** for marketers, business owners and students new to performance marketing, built by `funnel_magnet/ui/executive.py` from Python templates only. It answers three questions:

- **What changed?** A headline in business terms (how much recorded sales each unit of ad spend brought back), any problem hidden by stable or rising return on ad spend, ROAS and up to three KPIs that explain it, and a note when gains and losses largely cancel out.
- **What needs attention?** Every KPI that worsened materially, what its movement did to the business, measured, and why it matters, plus each kind of data warning in plain English.
- **What should I check next?** The top three checks, each with what to check, the evidence to look for, the possible cause it would point to, and the action to consider if the evidence supports it. Other possible results and what to do if the result is unclear are folded away. Funnel problems also list the direct checks for that stage, after tracking.

It restates the same diagnosis as the detailed analysis and adds no finding. A cause whose usual explanation is contradicted by the measured KPIs isn't promoted, and near-identical causes are shown together as related. After AI prioritisation, the checks and their causes follow the AI's order, while the headline and figures stay as Python wrote them. The AI's own explanations are in the detailed analysis. A label says whether the figures are a synthetic sample or the user's uploaded file.

Each KPI has a small **i** icon that opens its full name, meaning, formula and an example with this comparison's own figures.

Everything else is in the collapsed **Technical details** section, in four tabs: **Numbers** (confirmed findings, all KPI cards, the decision tree, the contribution chart and table), **Playbook** (the four-part diagnosis's causes, checks and actions), **Data quality** and **Glossary** (every KPI and funnel step, how they fit together, and the diagnostic path in figures). The sidebar keeps the data source and the comparison, and puts the materiality threshold and the blank-cells setting for uploads under **Advanced**. The report shows the executive diagnosis first, then the complete detailed analysis.

The detailed analysis holds one four-part diagnosis, built by `funnel_magnet/playbook.py` with no AI:

- **A. Confirmed findings:** what the data establishes, including each KPI that worsened or improved materially, its path through the tree and its effect on ROAS. Improvements are reported, with no causes suggested.
- **B. Possible causes:** causes from the marketing playbook for each KPI that worsened, all worded as possibilities. One cause can cover several problems, and combination rules flag patterns such as CPM and CTR worsening together. CPC and CPA get their components checked instead of causes of their own.
- **C. What to investigate:** the evidence that would tell the causes apart, split into checks available in the upload and data to collect elsewhere.
- **D. Potential actions:** for each cause, what to do if the evidence supports it and what to do if it doesn't.

The playbook lives in `funnel_magnet/playbook.toml` and can be edited without changing code. The tests reject a playbook with an unhedged cause, an unknown metric or a missing reference.

## AI prioritisation

The diagnosis in sections A to D is complete without AI. **Prioritise with AI** asks a model to order and explain the playbook's possible causes and checks for the current comparison. It can't make a diagnosis of its own.

AI stays off until `FM_AI_ENABLED` is `true`, even if a key is present. When it is off or unavailable, no AI button is shown, only a one-line reason.

### What the AI may and may not do

The model receives ROAS's direction, the mathematical summary, each problem and improvement with its effect on ROAS and its role (positive, adverse or offsetting), and the playbook's causes and checks. Only totals and playbook text are sent, never CSV rows, campaign names or file names.

It may return:

- an order for the playbook's causes, with a short explanation of each
- an order for the playbook's checks, with a short explanation of each
- how several problems might relate, when there is more than one
- guidance for each action, which must start with "If" and depend on what the checks show

It can't add causes, checks or findings. The reply may only use the cause and check IDs it was given, and any internal name it writes is replaced with the readable title.

`funnel_magnet/ai/prioritise.py` withholds any explanation that:

- states a number that isn't in the evidence
- presents a cause as fact, for example "caused", "drove" or "due to" without "may" or "could"
- says ROAS or a KPI moved the opposite way to the calculation, for example "the ROAS decline" when ROAS rose
- says a check would prove or confirm a cause, rather than support or weaken it
- claims a metric moved that isn't in the evidence, such as frequency
- says the problems share a cause, rather than that they might
- gives an action that isn't conditional on the checks

What is withheld is listed in the AI panel and the report, and logged with the failing text under the `funnel_magnet.ai.review` logger for development review. The log holds only the AI's wording, never data rows, campaign names or keys.

A funnel problem always keeps a tracking check in the top three, whatever the AI suggests. If the model fails, times out, reaches a usage limit or returns nothing usable, the page says so and the diagnosis stays as it was.

### Usage limits

Each browser session allows 3 AI analyses, and the page shows how many are left. A failed request counts too. Every visitor also shares one allowance of one request a minute and 25 a day. One request uses up to about 6,000 tokens, so this stays inside Groq's published free-plan limits of 8,000 tokens a minute and 200,000 a day. Check your own organisation's limits on Groq's Limits page.

These counters are held in the app's memory. A restart, a redeployment or the app going to sleep resets them, and a reloaded page is a new session with 3 analyses again. They reduce use but are not a guaranteed usage or billing cap. When Groq itself refuses a request because of its limits, the app shows the suggested wait and does not retry.

### Set up Groq

Groq is the provider tested with prioritisation, using `openai/gpt-oss-120b`.

1. Create a key in GroqCloud.
2. Store it as a Windows user environment variable named `GROQ_API_KEY`: open **Edit environment variables for your account**, add the variable, then restart the app. This keeps the key out of synced folders and shell history. Alternatively, add it to `.streamlit/secrets.toml`, which Git ignores.
3. Add `AI_PROVIDER = "groq"` and `FM_AI_ENABLED = "true"` to `.streamlit/secrets.toml`.

Groq requests use strict structured output, low reasoning effort and at most 3,000 completion tokens. A rate-limited request is not retried, and the message gives Groq's suggested wait. Groq checks the reply format after generating, so it can refuse a reply. The app then reads the refused reply and applies every usual check.

Groq works in the public deployment within the shared limits above.

### Gemini

Gemini remains available with `AI_PROVIDER = "gemini"` and `GEMINI_API_KEY`. It hasn't been tested live with prioritisation.

Google's Gemini API terms require paid use for an app made available to users in the EEA, Switzerland or the UK. Before sharing the app with those users, enable billing and set `GEMINI_TIER = "paid"` and `FM_SHARED_DEPLOYMENT = "true"`. These settings don't check billing, so confirm it in Google's console. Don't send real client data through the free tier.

| Setting | Default | Purpose |
|---|---|---|
| `FM_AI_ENABLED` | `false` | Must be `true` before any live AI call is possible |
| `AI_PROVIDER` | `gemini` | `gemini` or `groq` |
| `GROQ_API_KEY` | None | Server-side Groq key |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model name |
| `GEMINI_API_KEY` | None | Server-side Gemini key |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Gemini model name |
| `GEMINI_TIER` | `free` | Set to `paid` once the Google project has billing enabled |
| `FM_SHARED_DEPLOYMENT` | `false` | Set to `true` for a shared app. Gemini then needs `GEMINI_TIER = "paid"`. |

Never commit a key or share it in chat. To add a provider, add a class with `generate()` to `funnel_magnet/ai/providers.py` and return it from `create_provider()` in `funnel_magnet/ai/service.py`.

### Limitations

- The checks use rules and word patterns, so they can miss an unusual unsupported claim, or withhold a sound one. A number that happens to match the evidence also passes.
- Direction checks cover ROAS and the main KPIs by name. A direction word counts only when it sits next to the KPI, as in "CPM rose" or "higher CPM", so in "higher CPM and lower CTR" each KPI gets its own direction. "CTR may have fallen" is checked as a claim. A possible worsening of CPM, CTR or AOV after it improved, such as "saturation could lower CTR" when CTR rose, can't explain the problem and is withheld, unless it is a risk for later, such as "CTR could fall if saturation increases". ROAS, CPA, CPC and CVR are exempt, because a worsening component can pull them down even while they improve overall. Funnel stages are written too many ways to check reliably.
- The checks can't judge whether the AI's marketing reasoning is good, only whether it states something false or unsupported.
- AI output is a suggestion for a person to review. The app never changes an ad account.

## Diagnostic report

**Download Diagnostic Report** on the dashboard saves one HTML file for the current comparison. It opens in any modern browser with no server, and it has no scripts, outside stylesheets or outside requests. Use the browser's print command to print it.

`funnel_magnet/ui/report.py` builds the report from the same diagnosis object the page shows, including any AI prioritisation, and it calculates no metrics. All text from uploads and the model is escaped. Keys and settings are never passed to it.

- **No AI:** the report is complete, and says it contains no AI interpretation.
- **With AI:** section 5 names the provider and model, and the AI notes appear in sections B to D with their labels. If anything was withheld, a notice at the top says so.

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

An attribution-delay warning appears only when the current period ended recently. If the file states its attribution window, such as 7-day click, the warning appears while the period is inside that window on the analysis date, which is today by default. If the file doesn't state it, a period that ended fewer than seven days before the analysis gets a note that completeness can't be verified. Seven days is a cut-off for that note, not an assumed window, and an older period isn't guaranteed to be complete. The analysis date affects nothing else.

Each investigation in the playbook says what to look at, what different results could mean, and what to check next if the result is unclear. These readings are possibilities, never proof of a cause.

A blank cell is not treated as zero. A missing funnel value excludes that stage from any period containing it, and a blank in link clicks, purchases or purchase value blocks the file. Only if the user confirms that blanks mean 0 does `load_meta_csv(source, blank_actions_as_zero=True)` read them as 0.

An account-level export with no campaign column is analysed as one total labelled "All campaigns", with a warning, and no campaign breakdown is offered. When both "Website landing page views" and "Landing page views" are exported, the funnel uses the website column, and any row where the two differ is reported as a warning.

Dates and number formats are read at file level. A file is accepted only when one reading fits every value, so `13/06/2026` settles the day and month order for the whole file. If two readings remain possible, or none fits, the file is rejected with a reason.

The engine reports mathematical drivers only. It does not state causes.

## Sample data

`data/samples/` holds seven invented datasets and `expected_results.json`, which declares the expected result of each one by hand. To regenerate them:

```powershell
& "$env:USERPROFILE\.venvs\funnel-magnet\Scripts\python.exe" data/samples/generate_samples.py
```
