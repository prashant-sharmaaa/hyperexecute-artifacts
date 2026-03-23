# HyperExecute Artifact Downloader

A Python script to download all test artifacts — videos, screenshots, and logs — for every session inside a HyperExecute Job. Supports both **Web/Selenium** and **Real Device (RMAA)** sessions automatically.

---

## Prerequisites

- Python 3.10+
- `requests` library

```bash
pip install requests
```

---

## Setup

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `LT_USERNAME` | ✅ | Your LambdaTest username |
| `LT_ACCESS_KEY` | ✅ | Your LambdaTest access key |
| `HYEX_JOB_ID` | ✅ * | HyperExecute Job UUID (e.g. `5465f6e6-cd0b-494a-aa4f-72796ead071e`) |
| `HYEX_JOB_NUMBER` | ✅ * | HyperExecute Job Number (e.g. `7759`) |
| `ARTIFACT_DIR` | ❌ | Base output folder (default: current directory) |

> \* Set **either** `HYEX_JOB_ID` or `HYEX_JOB_NUMBER` — not both. If both are set, `HYEX_JOB_ID` takes priority.

You can find your `LT_USERNAME` and `LT_ACCESS_KEY` at [accounts.lambdatest.com/security](https://accounts.lambdatest.com/security).

---

## Usage

### Mac / Linux

```bash
# Using Job ID
export LT_USERNAME="your-username"
export LT_ACCESS_KEY="your-access-key"
export HYEX_JOB_ID="5465f6e6-cd0b-494a-aa4f-72796ead071e"

python3 hyperexecute_artifact.py
```

```bash
# Using Job Number
export LT_USERNAME="your-username"
export LT_ACCESS_KEY="your-access-key"
export HYEX_JOB_NUMBER="7759"

python3 hyperexecute_artifact.py
```

```bash
# Save to a custom folder
export ARTIFACT_DIR="/Users/you/Desktop/lt-artifacts"
python3 hyperexecute_artifact.py
```

### Windows (PowerShell)

```powershell
$env:LT_USERNAME="your-username"
$env:LT_ACCESS_KEY="your-access-key"
$env:HYEX_JOB_NUMBER="7759"

python hyperexecute_artifact.py
```

### Windows (CMD)

```cmd
set LT_USERNAME=your-username
set LT_ACCESS_KEY=your-access-key
set HYEX_JOB_NUMBER=7759

python hyperexecute_artifact.py
```

---

## Output Structure

All artifacts are saved inside a folder named after the **Job UUID**, regardless of whether you passed a Job ID or Job Number.

```
{JOB_UUID}/
├── session_001_<test_id>/
│   ├── video.mp4
│   ├── screenshots.zip
│   ├── selenium.log        ← Web/Selenium sessions
│   ├── console.log
│   ├── network.log
│   └── command.log
├── session_002_<test_id>/
│   ├── video.mp4
│   ├── screenshots.zip
│   ├── appium.log          ← Real Device sessions (RMAA)
│   ├── device.log
│   ├── crash.log
│   ├── network.log
│   └── console.log
└── artifact_summary.json
```

Running the script multiple times for different jobs keeps everything cleanly separated:

```
~/artifacts/
├── 5465f6e6-cd0b-494a-aa4f-72796ead071e/   ← Job 7759
├── a1b2c3d4-xxxx-xxxx-xxxx-xxxxxxxxxxxx/   ← Job 7760
└── ...
```

---

## How It Works

### API Flow

```
Step 1  →  GET https://api.hyperexecute.cloud/v2.0/job/{jobID}/sessions
           Fetch all sessions for the HyperExecute job

Step 2  →  GET /sessions/{test_id}
           Fetch full session detail — contains pre-built URLs for all artifacts

Step 3  →  Download everything using URLs from the session detail response:
           • video_url          → video.mp4
           • screenshot_url     → screenshots.zip
           • selenium_logs_url  → selenium.log
           • console_logs_url   → console.log
           • network_logs_url   → network.log
           • command_logs_url   → command.log
           • appium_logs_url    → appium.log    (Real Device only)
           • device_logs_url    → device.log    (Real Device only)
           • crash_logs_url     → crash.log     (Real Device only)
```

### Session Type Routing

The script automatically detects session type from the Test ID prefix and routes to the correct API:

| Test ID Prefix | Session Type | API Base URL |
|---|---|---|
| `RMAA-AND-` | Real Device – Android | `mobile-api.lambdatest.com` |
| `RMAA-IOS-` | Real Device – iOS | `mobile-api.lambdatest.com` |
| Anything else | Web / Selenium | `api.lambdatest.com` |

### Job Number Resolution

When `HYEX_JOB_NUMBER` is used instead of `HYEX_JOB_ID`, the script resolves the UUID in two steps:

1. `GET /v2.0/jobs?jobNumber={n}` — direct lookup (fastest)
2. If the above is not supported, scans paginated job list (`/v2.0/jobs?page=N&perPage=50`) across up to 20 pages

---

## artifact_summary.json

After each run, a summary report is written to the root of the output folder:

```json
{
  "job_id": "5465f6e6-cd0b-494a-aa4f-72796ead071e",
  "sessions": 9,
  "results": [
    {
      "test_id": "M1EH6-3AAHV-3JGGN-YAARK",
      "name": "Complete KYC Registration",
      "status": "passed",
      "downloaded": ["video", "screenshots", "selenium.log", "console.log"],
      "skipped": []
    }
  ]
}
```

---

## Use in CI / HyperExecute Pipeline

To run the downloader automatically after every test execution, include the YAML pipeline config:

```yaml
# hyperexecute-artifact-download.yaml
pre:
  - pip install requests --quiet

testSuites:
  - name: download-artefacts
    command: python3 hyperexecute_artifact.py
    env:
      LT_USERNAME: ${LT_USERNAME}
      LT_ACCESS_KEY: ${LT_ACCESS_KEY}
      HYEX_JOB_ID: ${HYEX_JOB_ID}

artefacts:
  upload:
    - name: test-run-artefacts
      path:
        - ${HYEX_JOB_ID}/
      expiresIn: 7d
```

---

## Troubleshooting

**`KeyError: 'LT_USERNAME'`**
→ Environment variables are not set. Make sure to `export` them before running.

**`HTTP 404` on session detail**
→ The Test ID could not be resolved correctly. Run the script once and check the `Raw session[0] fields` debug output to identify the correct field name.

**`HTTP 401 Unauthorized`**
→ Check your `LT_USERNAME` and `LT_ACCESS_KEY`. Keys are available at [accounts.lambdatest.com/security](https://accounts.lambdatest.com/security).

**Job Number not resolving**
→ The script scans up to 1,000 recent jobs. If your job is older, use `HYEX_JOB_ID` directly instead.

**Empty log files**
→ Logs may not be available for very short sessions or sessions that were aborted early.

---

## Files

| File | Description |
|---|---|
| `hyperexecute_artifact.py` | Main downloader script |
| `hyperexecute-artifact-download.yaml` | HyperExecute pipeline YAML for CI usage |
| `README.md` | This file |
