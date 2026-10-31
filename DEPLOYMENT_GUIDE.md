# TLT/TBT Streamlit Cloud Deployment

Place this directory at:

`C:\TLT-TBT-Trading System\Cloud`

This is a separate Git repository containing only the files required by the
client-facing Streamlit dashboards.

## Five apps in one repository

| Dashboard | Streamlit entrypoint |
|---|---|
| TLT Daily | `app_tlt_actionable_daily_fast.py` |
| TLT Weekly | `app_tlt_actionable_weekly_fast.py` |
| TBT Daily | `app_tbt_actionable_daily_fast.py` |
| TBT Weekly | `app_tbt_actionable_weekly_fast.py` |
| Market Pulse | `app_market_pulse.py` |

All five apps share:

`data/actionable_cloud.sqlite`

The large master/research database is intentionally excluded.

## First deployment

### 1. Extract

Extract the downloaded ZIP into:

`C:\TLT-TBT-Trading System`

You should end up with:

`C:\TLT-TBT-Trading System\Cloud`

### 2. Prepare and validate the compact DB

```bat
cd /d "C:\TLT-TBT-Trading System\Cloud"
01_PREPARE_CLOUD.bat
```

Do not continue unless you see:

`CLOUD PACKAGE VALIDATION: PASS`

### 3. Create an empty GitHub repository

Suggested name:

`tlt-tbt-client-cloud`

Create it EMPTY:
- no README
- no .gitignore
- no license

### 4. Push the Cloud folder to GitHub

```bat
02_GITHUB_FIRST_DEPLOY.bat
```

Paste the repository HTTPS URL when requested.

### 5. Deploy in Streamlit Community Cloud

Go to:

https://share.streamlit.io

For each dashboard:
1. Click **Create app**.
2. Choose **Yup, I have an app**.
3. Select the same GitHub repository.
4. Branch: `main`.
5. Select the appropriate entrypoint below.
6. Open **Advanced settings**.
7. Select **Python 3.10**.
8. Click **Deploy**.

Deploy these five entrypoints:

```text
app_tlt_actionable_daily_fast.py
app_tlt_actionable_weekly_fast.py
app_tbt_actionable_daily_fast.py
app_tbt_actionable_weekly_fast.py
app_market_pulse.py
```

You can choose a separate Streamlit subdomain for each app.

## Routine update after each successful EOD run

```bat
cd /d "C:\TLT-TBT-Trading System\Cloud"
03_PUSH_CLOUD_UPDATE.bat
```

This:
1. refreshes/copies `actionable_cloud.sqlite`,
2. validates the package,
3. commits changes,
4. pushes `main`.

Streamlit Community Cloud will detect the GitHub update and refresh the apps.

## Do not push these large/local files

Do NOT put these into the Cloud repo:
- `marketvector_tlt_tbt.sqlite`
- `ibkr_market_data.sqlite`
- local logs
- IBKR/TWS updater scripts
- large historical/research databases

Git LFS is configured for `data/*.sqlite`.

## Local tests

After `01_PREPARE_CLOUD.bat`:

```text
10_TEST_TLT_DAILY.bat
11_TEST_TLT_WEEKLY.bat
12_TEST_TBT_DAILY.bat
13_TEST_TBT_WEEKLY.bat
14_TEST_MARKET_PULSE.bat
```
