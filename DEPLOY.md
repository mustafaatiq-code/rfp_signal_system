# Deployment Guide

How to host the **Opportunity Signal Radar** dashboard on
[Streamlit Community Cloud](https://streamlit.io/cloud) and keep its data fresh.

---

## Architecture at a glance

```
  run_pipeline.py --live   →   data/db/opportunities.sqlite3   →   dashboard (Streamlit Cloud)
     (runs anywhere)              (published snapshot)                (read-only viewer)
```

The dashboard is a **read-only viewer**. It never scrapes; it only reads the
SQLite database. Fresh data is produced by running the pipeline and publishing
the resulting database snapshot (see [Refreshing data](#refreshing-the-data)).

---

## One-time setup

### 1. Push the repo to GitHub
The repo is already on GitHub. Streamlit Cloud deploys directly from it.

### 2. Publish an initial data snapshot
The working database is **gitignored** so routine development doesn't churn
binary blobs into history. To give the hosted dashboard something to show, you
publish a snapshot *deliberately*:

```bash
python run_pipeline.py --live          # generate fresh data locally
git add -f data/db/opportunities.sqlite3
git commit -m "Publish data snapshot"
git push
```

The `-f` is required precisely because the file is gitignored — it makes
publishing an intentional act, not an accident.

### 3. Create the Streamlit Cloud app
1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. **New app** → pick this repo, branch `main`, main file `output/dashboard.py`.
3. Deploy. Streamlit installs `requirements.txt` automatically.

### 4. Set the app secrets
In the app: **Manage app → Settings → Secrets**, paste:

```toml
dashboard_password = "choose-a-shared-password"
deployed = "true"
```

- `dashboard_password` — password everyone at GMG uses to open the dashboard.
  Leave it out to disable the gate entirely.
- `deployed = "true"` — hides the local-only **Refresh Data** button (that button
  runs the live pipeline, which can't work on Cloud's read-only filesystem).

See [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example) for the
same template.

---

## Refreshing the data

### Automatic (default)

[.github/workflows/refresh-data.yml](.github/workflows/refresh-data.yml) runs the
live pipeline **daily** (11:00 UTC) and pushes a fresh database snapshot to
`main`. Streamlit Cloud auto-redeploys on the push, so the dashboard stays
current with no manual steps. You can also trigger it any time from the repo's
**Actions** tab → *Refresh Data* → *Run workflow*.

To include SAM.gov federal records in the automated refresh, add the API key as a
repository secret: **Settings → Secrets and variables → Actions → New repository
secret**, name `SAM_GOV_API_KEY`. Without it, SAM.gov is skipped and every other
source still refreshes.

> **Note on history:** each refresh commits the binary `.sqlite3` snapshot to
> `main`, so history grows over time. That's an accepted trade-off for the
> zero-infrastructure Streamlit Cloud setup. If it becomes noisy, point the
> Streamlit app at a dedicated `data` branch and change the workflow's push
> target to match.

### Manual (fallback)

If you need to publish a snapshot immediately:

```bash
python run_pipeline.py --live
git add -f data/db/opportunities.sqlite3
git commit -m "Refresh data snapshot $(date +%F)"
git push
```

---

## Local development

No secrets needed. With no `dashboard_password` set, the login gate is disabled
and the **Refresh Data** button is shown:

```bash
pip install -r requirements-dev.txt
playwright install chromium
python run_pipeline.py --live
python -m streamlit run output/dashboard.py
```

To test the password gate locally, copy the template and set a value:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edit dashboard_password in that file (it is gitignored)
```

---

## Continuous integration

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs the full test suite on
every push and pull request to `main`. All 45 tests are offline-capable, so CI
needs no secrets, API keys, or network access.
