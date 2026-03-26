# HyperExecute Artifact Downloader

Downloads video, screenshots, and logs for every session in a HyperExecute job. Triggered via GitLab CI/CD.

---

## Files

| File | Description |
|---|---|
| `hyperexecute_artifacts.py` | Artifact downloader script |
| `.gitlab-ci.yml` | GitLab CI/CD pipeline |

---

## Setup

### Secrets — GitLab → Settings → CI/CD → Variables

| Variable | Description |
|---|---|
| `LT_USERNAME` | LambdaTest username |
| `LT_ACCESS_KEY` | LambdaTest access key |
| `LT_AUTH_TOKEN` | Base64 token from `Authorization: Basic` header |
| `TEST_RUN_ID` | ATM test run ID e.g. `01KM07523T69W590S0DFWX69FP` |

---

## Output Structure

```
{JOB_UUID}/
├── session_001_<test_id>/
│   ├── video.mp4
│   ├── screenshots.zip
│   ├── selenium.log       ← Web sessions
│   ├── console.log
│   ├── network.log
│   └── command.log
├── session_002_<test_id>/
│   ├── video.mp4
│   ├── screenshots.zip
│   ├── appium.log         ← Real Device sessions (RMAA)
│   ├── device.log
│   └── crash.log
└── artifact_summary.json
```

---

## Troubleshooting

| Error | Fix |
|---|---|
| `KeyError: LT_USERNAME` | Secrets not set in GitLab CI/CD variables |
| `HTTP 401` | Check `LT_USERNAME`, `LT_ACCESS_KEY`, `LT_AUTH_TOKEN` |
| `job_id not found` | Check `TEST_RUN_ID` is correct |
| Job polling timeout | Increase `POLL_TIMEOUT` variable (default: 3600s) |
