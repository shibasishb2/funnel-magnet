# Funnel Magnet

AI Marketing Performance Diagnostic Agent: an ESCP MBA prototype that diagnoses changes in Meta Ads performance.

**Status:** Stage 0 complete. The app shell runs, and diagnostic features are not built yet.

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

## Requirements for Stage 1

- The materiality threshold is configurable, with a default of 5%.
- Periods with zero purchases, zero spend or undefined metrics show those metrics as undefined and do not produce misleading contribution figures.
