#!/usr/bin/env python3
"""
hyperexecute_artifacts.py
─────────────────────────
HyperExecute + LambdaTest artifact downloader — CI/CD mode.

Reads job_id from the curl trigger response piped via stdin:
  curl ... | python3 hyperexecute_artifacts.py

Flow:
  1. Read job_id from stdin (curl trigger response)
  2. Poll job status until complete
  3. Fetch all sessions for the job
  4. For each session, fetch session detail (contains all artifact URLs)
  5. Download video, screenshots, and logs
  6. Download job-level HTML report

Session type routing (auto-detected from test ID prefix):
  RMAA-AND-*  →  Real Device Android  →  mobile-api.lambdatest.com
  RMAA-IOS-*  →  Real Device iOS      →  mobile-api.lambdatest.com
  Everything  →  Web / Selenium       →  api.lambdatest.com

Output folder:
  {JOB_ID}/
  ├── report.html
  ├── session_001_<test_id>/
  │   ├── video.mp4
  │   ├── screenshots.zip
  │   ├── selenium.log      (Web)  | appium.log   (Real Device)
  │   ├── console.log              | device.log
  │   ├── network.log              | crash.log
  │   └── command.log              | network.log + console.log
  └── artifact_summary.json

Required env vars:
  LT_USERNAME    – LambdaTest username
  LT_ACCESS_KEY  – LambdaTest access key

Optional:
  ARTIFACT_DIR   – Base output folder (default: current directory)
  POLL_INTERVAL  – Seconds between job status polls (default: 15)
  POLL_TIMEOUT   – Max seconds to wait for job completion (default: 3600)
"""

import os
import sys
import json
import time
import requests
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
USERNAME      = os.environ["LT_USERNAME"]
ACCESS_KEY    = os.environ["LT_ACCESS_KEY"]
ARTIFACT_DIR  = os.environ.get("ARTIFACT_DIR", ".").strip()
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "15"))
POLL_TIMEOUT  = int(os.environ.get("POLL_TIMEOUT", "3600"))
AUTH          = (USERNAME, ACCESS_KEY)

HYEX_BASE          = "https://api.hyperexecute.cloud/v2.0"
LT_WEB_BASE        = "https://api.lambdatest.com/automation/api/v1"
LT_REAL_DEV_BASE   = "https://mobile-api.lambdatest.com/mobile-automation/api/v1"

# Log fields per session type → (response_key, output_filename)
WEB_LOG_FIELDS = [
    ("selenium_logs_url", "selenium.log"),
    ("console_logs_url",  "console.log"),
    ("network_logs_url",  "network.log"),
    ("command_logs_url",  "command.log"),
]
REAL_DEV_LOG_FIELDS = [
    ("appium_logs_url",  "appium.log"),
    ("device_logs_url",  "device.log"),
    ("crash_logs_url",   "crash.log"),
    ("network_logs_url", "network.log"),
    ("console_logs_url", "console.log"),
]


# ── Session type helpers ──────────────────────────────────────────────────────
def get_base_url(test_id: str) -> str:
    if str(test_id).upper().startswith("RMAA"):
        return LT_REAL_DEV_BASE
    return LT_WEB_BASE

def session_type(test_id: str) -> str:
    tid = str(test_id).upper()
    if tid.startswith("RMAA-AND"):
        return "Real Device - Android"
    if tid.startswith("RMAA-IOS"):
        return "Real Device - iOS"
    if tid.startswith("RMAA"):
        return "Real Device - Mobile App"
    return "Web/Selenium"

def get_log_fields(test_id: str) -> list:
    if str(test_id).upper().startswith("RMAA"):
        return REAL_DEV_LOG_FIELDS
    return WEB_LOG_FIELDS


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def get(url: str):
    """GET with retry. Returns parsed JSON (list or dict)."""
    for attempt in range(3):
        try:
            r = requests.get(url, auth=AUTH, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            print(f"  [WARN] HTTP {r.status_code} on {url}: {e}")
            if r.status_code in (429, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return {}
        except Exception as e:
            print(f"  [WARN] Request failed ({attempt+1}/3): {e}")
            time.sleep(3)
    return {}

def download_binary(url: str, dest: Path, label: str) -> bool:
    """Download a binary file (video, zip). Tries with auth, then without."""
    try:
        for auth in [AUTH, None]:
            with requests.get(url, auth=auth, stream=True, timeout=180) as r:
                if r.status_code == 401 and auth is not None:
                    continue
                r.raise_for_status()
                dest.write_bytes(r.content)
                print(f"    ✓ {label} → {dest.name} ({dest.stat().st_size/1024:.1f} KB)")
                return True
    except Exception as e:
        print(f"    ✗ {label} failed: {e}")
    return False

def download_log(url: str, dest: Path, label: str) -> bool:
    """Download a log endpoint — handles JSON array, JSON object, or plain text."""
    try:
        for auth in [AUTH, None]:
            r = requests.get(url, auth=auth, timeout=60)
            if r.status_code == 401 and auth is not None:
                continue
            r.raise_for_status()
            ct = r.headers.get("Content-Type", "")
            if "json" in ct:
                data = r.json()
                if isinstance(data, list):
                    lines = []
                    for entry in data:
                        if isinstance(entry, str):
                            lines.append(entry)
                        elif isinstance(entry, dict):
                            ts  = entry.get("timestamp") or entry.get("time") or ""
                            msg = entry.get("message") or entry.get("log") or json.dumps(entry)
                            lines.append(f"[{ts}] {msg}" if ts else msg)
                    dest.write_text("\n".join(lines), encoding="utf-8")
                elif isinstance(data, dict):
                    inner = data.get("data") or data.get("logs") or data.get("result") or data
                    if isinstance(inner, str):
                        dest.write_text(inner, encoding="utf-8")
                    elif isinstance(inner, list):
                        dest.write_text(
                            "\n".join(e if isinstance(e, str) else json.dumps(e) for e in inner),
                            encoding="utf-8"
                        )
                    else:
                        dest.write_text(json.dumps(inner, indent=2), encoding="utf-8")
                else:
                    dest.write_text(str(data), encoding="utf-8")
            else:
                dest.write_text(r.text, encoding="utf-8")
            print(f"    ✓ {label} → {dest.name} ({dest.stat().st_size/1024:.1f} KB)")
            return True
    except Exception as e:
        print(f"    ✗ {label} failed: {e}")
    return False

def _extract_list(data, *keys) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            val = data.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                for k2 in keys:
                    inner = val.get(k2)
                    if isinstance(inner, list):
                        return inner
    return []


# ── Step 0: Read job_id from stdin ────────────────────────────────────────────
def read_job_id_from_stdin() -> str:
    """
    Read the curl trigger response from stdin and extract job_id.

    Expected JSON:
    {
        "job_id": "dabdfe19-dacc-428b-9e1d-1bfcd67d9d09",
        "test_run_id": "01KMFR10KHY84AEFFNRMB50ASY",
        "job_link": "https://hyperexecute.lambdatest.com/...",
        ...
    }
    """
    print("[Step 0] Reading trigger response from stdin ...")
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            print("[ERROR] stdin is empty. Pipe the curl trigger response into this script.")
            print("        curl ... | LT_USERNAME=x LT_ACCESS_KEY=y python3 hyperexecute_artifacts.py")
            sys.exit(1)

        data   = json.loads(raw)
        job_id = (
            data.get("job_id")
            or data.get("jobId")
            or data.get("jobID")
            or data.get("id")
        )

        if not job_id or not isinstance(job_id, str) or len(job_id) < 10:
            print(f"[ERROR] job_id not found in trigger response.")
            print(f"        Keys received: {list(data.keys())}")
            print(f"        Full response: {json.dumps(data, indent=8)}")
            sys.exit(1)

        job_id = job_id.strip()
        print(f"         Job ID   : {job_id}")
        if data.get("job_link"):
            print(f"         Job link : {data['job_link']}")
        if data.get("test_run_id"):
            print(f"         Run ID   : {data['test_run_id']}")
        return job_id

    except json.JSONDecodeError as e:
        print(f"[ERROR] Could not parse stdin as JSON: {e}")
        print(f"        Raw input: {raw[:300]}")
        sys.exit(1)


# ── Step 1: Poll until job completes ─────────────────────────────────────────
def wait_for_job_completion(job_id: str) -> str:
    """
    Poll GET /v2.0/job/{job_id} every POLL_INTERVAL seconds until
    the job reaches a terminal state or POLL_TIMEOUT is exceeded.
    """
    terminal_states = {
        "completed", "passed", "failed", "cancelled",
        "skipped", "partial", "error", "timeout"
    }
    url     = f"{HYEX_BASE}/job/{job_id}"
    elapsed = 0

    print(f"\n[Step 1] Polling job status every {POLL_INTERVAL}s (timeout: {POLL_TIMEOUT//60}m) ...")

    while elapsed < POLL_TIMEOUT:
        data  = get(url)
        inner = (data.get("data") or data) if isinstance(data, dict) else {}
        status = (
            inner.get("status")
            or inner.get("jobStatus")
            or inner.get("job_status")
            or ""
        ).lower()

        print(f"  [{elapsed:>5}s] Status: {status or 'unknown'}")

        if status in terminal_states:
            print(f"         Terminal state reached: '{status}'")
            return status

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    print(f"[WARN] Timed out after {POLL_TIMEOUT}s. Proceeding — some artifacts may be incomplete.")
    return "timeout"


# ── Step 2: Fetch all sessions for the job ────────────────────────────────────
def fetch_sessions(job_id: str) -> list:
    print(f"\n[Step 2] Fetching sessions for Job: {job_id}")
    data     = get(f"{HYEX_BASE}/job/{job_id}/sessions")
    sessions = _extract_list(data, "sessions", "data", "result", "items")
    print(f"         Found {len(sessions)} session(s).")
    if sessions:
        print(f"         Session fields: {list(sessions[0].keys())}")
    return sessions

def get_test_id(session: dict) -> str | None:
    for key in ("testID", "test_id", "testId", "sessionID", "session_id", "sessionId"):
        val = session.get(key)
        if val and isinstance(val, str) and len(val) > 5:
            return str(val)
    return None


# ── Step 3: Fetch session detail (contains all artifact URLs) ─────────────────
def fetch_session_detail(test_id: str) -> dict | None:
    base = get_base_url(test_id)
    url  = f"{base}/sessions/{test_id}"
    print(f"  [Step 3] Fetching session detail ...")
    print(f"           Type     : {session_type(test_id)}")
    print(f"           Endpoint : {url}")
    resp = get(url)
    if not resp:
        return None
    return resp.get("data") or resp


# ── Step 4: Download all artifacts for a session ──────────────────────────────
def download_session_artifacts(test_id: str, detail: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"downloaded": [], "skipped": []}

    # Video
    video_url = detail.get("video_url")
    if video_url:
        if download_binary(video_url, out_dir / "video.mp4", "Video"):
            summary["downloaded"].append("video.mp4")
        else:
            summary["skipped"].append("video.mp4")
    else:
        print(f"    – video_url not in session detail.")
        summary["skipped"].append("video.mp4")

    # Screenshots ZIP
    screenshot_url = detail.get("screenshot_url")
    if screenshot_url:
        if download_binary(screenshot_url, out_dir / "screenshots.zip", "Screenshots"):
            summary["downloaded"].append("screenshots.zip")
        else:
            summary["skipped"].append("screenshots.zip")
    else:
        print(f"    – screenshot_url not in session detail.")
        summary["skipped"].append("screenshots.zip")

    # Logs
    print(f"  [Step 4] Downloading logs ({session_type(test_id)}) ...")
    for url_field, filename in get_log_fields(test_id):
        log_url = detail.get(url_field)
        if log_url:
            label = url_field.replace("_logs_url", "").replace("_url", "")
            if download_log(log_url, out_dir / filename, label):
                summary["downloaded"].append(filename)
            else:
                summary["skipped"].append(filename)
        else:
            print(f"    – {url_field} not available for this session.")
            summary["skipped"].append(filename)

    return summary


# ── Job-level HTML report ─────────────────────────────────────────────────────
def download_job_report(job_id: str, base_dir: Path) -> bool:
    """
    GET https://api-hyperexecute.lambdatest.com/logistics/v1.0/report/{job_id}/download?type=default
    Response: {"data": "<signed-url-to-report.html>", "status": "success"}
    Downloads the HTML report into {base_dir}/report.html
    """
    print("\n[Report] Fetching job report ...")
    url  = f"https://api-hyperexecute.lambdatest.com/logistics/v1.0/report/{job_id}/download?type=default"
    resp = get(url)

    if not resp:
        print(f"  [WARN] No response from report API.")
        return False

    signed_url = resp.get("data") if isinstance(resp, dict) else None
    if not signed_url or not isinstance(signed_url, str):
        print(f"  [WARN] Signed URL not found in report response.")
        print(f"         Response: {resp}")
        return False

    print(f"  Downloading report HTML ...")
    dest = base_dir / "report.html"
    try:
        # Signed URL — no auth header needed
        with requests.get(signed_url, timeout=60) as r:
            r.raise_for_status()
            dest.write_bytes(r.content)
        print(f"  ✓ Report saved → {dest.name} ({dest.stat().st_size/1024:.1f} KB)")
        return True
    except Exception as e:
        print(f"  ✗ Report download failed: {e}")
        return False


# ── Summary report ────────────────────────────────────────────────────────────
def write_summary(base_dir: Path, job_id: str, results: list):
    out = base_dir / "artifact_summary.json"
    out.write_text(json.dumps({
        "job_id"         : job_id,
        "total_sessions" : len(results),
        "results"        : results,
    }, indent=2))
    print(f"\n[Summary] Written → {out}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(" HyperExecute Artifact Downloader — CI/CD Mode")
    print("=" * 60)

    # Step 0 — get job_id from piped curl output
    job_id   = read_job_id_from_stdin()
    base_dir = Path(ARTIFACT_DIR) / job_id
    base_dir.mkdir(parents=True, exist_ok=True)
    print(f"         Output dir: {base_dir.resolve()}")

    # Step 1 — wait for job to finish
    job_status = wait_for_job_completion(job_id)
    if job_status in ("cancelled", "error"):
        print(f"[WARN] Job ended with '{job_status}'. Artifacts may be partial.")

    # Step 2 — fetch sessions
    sessions = fetch_sessions(job_id)
    if not sessions:
        print("[ERROR] No sessions returned. Check credentials and job ID.")
        sys.exit(1)

    # Steps 3 & 4 — per session: detail + download
    results = []
    for idx, session in enumerate(sessions, start=1):
        print(f"\n{'='*60}")
        print(f"[Session {idx}/{len(sessions)}]")

        test_id = get_test_id(session)
        if not test_id:
            print(f"  [SKIP] Could not resolve test ID from session: {session}")
            results.append({"error": "no test_id", "raw": session})
            continue

        print(f"  Test ID : {test_id}")

        detail = fetch_session_detail(test_id)
        if not detail:
            print(f"  [SKIP] No detail returned for {test_id}")
            results.append({"test_id": test_id, "error": "no detail returned"})
            continue

        print(f"           Name     : {detail.get('name', '-')}")
        print(f"           Status   : {detail.get('status_ind', '-')}")
        print(f"           Platform : {detail.get('platform', '-')}")
        print(f"           Browser  : {detail.get('browser', '-')} {detail.get('browser_version', '')}")

        out_dir    = base_dir / f"session_{idx:03d}_{test_id}"
        dl_summary = download_session_artifacts(test_id, detail, out_dir)

        results.append({
            "test_id"    : test_id,
            "name"       : detail.get("name"),
            "status"     : detail.get("status_ind"),
            "type"       : session_type(test_id),
            "downloaded" : dl_summary["downloaded"],
            "skipped"    : dl_summary["skipped"],
        })

    # Download job-level HTML report
    download_job_report(job_id, base_dir)

    write_summary(base_dir, job_id, results)

    print(f"\n{'='*60}")
    print(f"✅  All done → {base_dir.resolve()}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
