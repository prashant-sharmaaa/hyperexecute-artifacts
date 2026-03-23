#!/usr/bin/env python3
"""
download_artifacts.py
─────────────────────
HyperExecute + LambdaTest artifact downloader.

Flow:
  Step 1  – Fetch all sessions for the HyEx Job
  Step 2  – For each session, call GET /sessions/{test_id} to get all URLs
  Step 3  – Download video, screenshots (zip), and all logs from those URLs

Folder structure:
  {JOB_ID}/
  ├── session_001_<test_id>/
  │   ├── video.mp4
  │   ├── screenshots.zip
  │   ├── selenium.log
  │   ├── console.log
  │   ├── network.log
  │   └── command.log
  └── artifact_summary.json

Environment variables:
  LT_USERNAME    – LambdaTest username
  LT_ACCESS_KEY  – LambdaTest access key
  HYEX_JOB_ID    – HyperExecute Job ID
  ARTIFACT_DIR   – Base folder (default: current dir)
"""

import os
import sys
import json
import time
import requests
from pathlib import Path

USERNAME    = os.environ["LT_USERNAME"]
ACCESS_KEY  = os.environ["LT_ACCESS_KEY"]

# Accept either HYEX_JOB_ID (UUID) or HYEX_JOB_NUMBER (integer like 7759)
# If both are set, HYEX_JOB_ID takes priority
_RAW_JOB_ID     = os.environ.get("HYEX_JOB_ID", "").strip()
_RAW_JOB_NUMBER = os.environ.get("HYEX_JOB_NUMBER", "").strip()

AUTH        = (USERNAME, ACCESS_KEY)

HYEX_BASE            = "https://api.hyperexecute.cloud/v2.0"
LT_WEB_BASE          = "https://api.lambdatest.com/automation/api/v1"
LT_REAL_DEVICE_BASE  = "https://mobile-api.lambdatest.com/mobile-automation/api/v1"


def resolve_job_id() -> str:
    """
    Resolve the HyperExecute Job UUID from either:
      - HYEX_JOB_ID   → already a UUID, use directly
      - HYEX_JOB_NUMBER → integer job number, look up via API

    HyperExecute API options tried in order:
      1. GET /v2.0/jobs?jobNumber={n}        (direct query param, if supported)
      2. GET /v2.0/jobs?page=1&perPage=50   (paginated search as fallback)
    """
    if _RAW_JOB_ID:
        print(f"[Config] Using Job ID directly: {_RAW_JOB_ID}")
        return _RAW_JOB_ID

    if not _RAW_JOB_NUMBER:
        print("[ERROR] Set either HYEX_JOB_ID or HYEX_JOB_NUMBER environment variable.")
        sys.exit(1)

    job_number = int(_RAW_JOB_NUMBER)
    print(f"[Config] Resolving Job UUID for Job Number: {job_number} ...")

    # ── Attempt 1: direct query param ──────────────────────────────────────────
    url  = f"{HYEX_BASE}/jobs?jobNumber={job_number}"
    resp = _raw_get(url)
    job_id = _find_job_id_in_response(resp, job_number)
    if job_id:
        print(f"[Config] Resolved → Job ID: {job_id}")
        return job_id

    # ── Attempt 2: paginated search through recent jobs ────────────────────────
    print(f"         Direct query param not supported, scanning paginated jobs ...")
    for page in range(1, 20):           # scan up to 20 pages × 50 = 1000 jobs
        url  = f"{HYEX_BASE}/jobs?page={page}&perPage=50"
        resp = _raw_get(url)
        if not resp:
            break
        job_id = _find_job_id_in_response(resp, job_number)
        if job_id:
            print(f"[Config] Resolved → Job ID: {job_id}  (found on page {page})")
            return job_id
        # If no jobs returned, we've gone past all available pages
        jobs = _extract_list_static(resp, "jobs", "data", "result", "items")
        if not jobs:
            break

    print(f"[ERROR] Could not resolve Job UUID for Job Number {job_number}.")
    print(f"        Please set HYEX_JOB_ID directly instead.")
    sys.exit(1)


def _raw_get(url):
    """Minimal GET used before AUTH-dependent helpers are fully set up."""
    try:
        r = requests.get(url, auth=AUTH, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def _extract_list_static(data, *keys):
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


def _find_job_id_in_response(resp, job_number: int) -> str | None:
    """Look for a job matching job_number in an API response and return its UUID."""
    if not resp:
        return None

    # Case 1: response is a single job dict
    if isinstance(resp, dict):
        data = resp.get("data") or resp
        if isinstance(data, dict):
            # Single job returned directly
            num = data.get("jobNumber") or data.get("job_number") or data.get("number")
            uid = data.get("jobID") or data.get("job_id") or data.get("id")
            if num and int(num) == job_number and uid:
                return str(uid)
            # List of jobs wrapped in dict
            jobs = _extract_list_static(data, "jobs", "items", "result")
            return _scan_jobs_list(jobs, job_number)

    # Case 2: response is a bare list
    if isinstance(resp, list):
        return _scan_jobs_list(resp, job_number)

    return None


def _scan_jobs_list(jobs: list, job_number: int) -> str | None:
    for job in jobs:
        if not isinstance(job, dict):
            continue
        num = job.get("jobNumber") or job.get("job_number") or job.get("number")
        uid = job.get("jobID") or job.get("job_id") or job.get("id")
        if num is not None and int(num) == job_number and uid:
            return str(uid)
    return None

# Test ID prefix routing
# RMAA-AND = Real Mobile App Automation - Android
# RMAA-IOS = Real Mobile App Automation - iOS
# All RMAA-* variants use the mobile-api base URL

def get_base_url(test_id: str) -> str:
    """Route to the correct API base URL based on test ID prefix."""
    if str(test_id).upper().startswith("RMAA"):
        return LT_REAL_DEVICE_BASE
    return LT_WEB_BASE

def session_type(test_id: str) -> str:
    """Return a human-readable session type label from the test ID prefix."""
    tid = str(test_id).upper()
    if tid.startswith("RMAA-AND"):
        return "Real Device - Android"
    if tid.startswith("RMAA-IOS"):
        return "Real Device - iOS"
    if tid.startswith("RMAA"):
        return "Real Device - Mobile App"
    return "Web/Selenium"

# Resolve Job ID (from UUID or Job Number) and set BASE_DIR
JOB_ID   = resolve_job_id()
BASE_DIR = Path(os.environ.get("ARTIFACT_DIR", ".")) / JOB_ID
BASE_DIR.mkdir(parents=True, exist_ok=True)

# Log fields per session type: (response_key, output_filename)
# Web / Selenium sessions
WEB_LOG_FIELDS = [
    ("selenium_logs_url", "selenium.log"),
    ("console_logs_url",  "console.log"),
    ("network_logs_url",  "network.log"),
    ("command_logs_url",  "command.log"),
]

# Real Device (RMAA) sessions — Appium, device, crash logs
REAL_DEVICE_LOG_FIELDS = [
    ("appium_logs_url",       "appium.log"),
    ("device_logs_url",       "device.log"),
    ("crash_logs_url",        "crash.log"),
    ("network_logs_url",      "network.log"),
    ("console_logs_url",      "console.log"),
]

def get_log_fields(test_id: str) -> list:
    """Return the correct log field list based on session type."""
    if str(test_id).upper().startswith("RMAA"):
        return REAL_DEVICE_LOG_FIELDS
    return WEB_LOG_FIELDS


# ── Helpers ───────────────────────────────────────────────────────────────────
def get(url):
    """GET with retry – returns parsed JSON (list or dict)."""
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


def download_binary(url, dest, label):
    """Download any binary/text URL to dest. Uses credentials from AUTH."""
    try:
        # Some URLs (video, public screenshot zip) may already embed credentials
        # Try with auth first, fall back without if 401
        for auth in [AUTH, None]:
            with requests.get(url, auth=auth, stream=True, timeout=180) as r:
                if r.status_code == 401 and auth is not None:
                    continue
                r.raise_for_status()
                dest.write_bytes(r.content)
                size_kb = dest.stat().st_size / 1024
                print(f"    ✓ {label} → {dest.name} ({size_kb:.1f} KB)")
                return True
    except Exception as e:
        print(f"    ✗ {label} failed: {e}")
    return False


def download_log(url, dest, label):
    """Download a log endpoint – handles JSON array, JSON object, or plain text."""
    try:
        for auth in [AUTH, None]:
            r = requests.get(url, auth=auth, timeout=60)
            if r.status_code == 401 and auth is not None:
                continue
            r.raise_for_status()

            ct = r.headers.get("Content-Type", "")
            if "json" in ct:
                data = r.json()
                # Unwrap common shapes
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
                        dest.write_text("\n".join(
                            e if isinstance(e, str) else json.dumps(e) for e in inner
                        ), encoding="utf-8")
                    else:
                        dest.write_text(json.dumps(inner, indent=2), encoding="utf-8")
                else:
                    dest.write_text(str(data), encoding="utf-8")
            else:
                dest.write_text(r.text, encoding="utf-8")

            size_kb = dest.stat().st_size / 1024
            print(f"    ✓ {label} → {dest.name} ({size_kb:.1f} KB)")
            return True

    except Exception as e:
        print(f"    ✗ {label} failed: {e}")
    return False


def _extract_list(data, *keys):
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


# ── Step 1: Fetch HyEx sessions ───────────────────────────────────────────────
def fetch_sessions(job_id):
    print(f"\n[Step 1] Fetching sessions for HyEx Job: {job_id}")
    data     = get(f"{HYEX_BASE}/job/{job_id}/sessions")
    sessions = _extract_list(data, "sessions", "data", "result", "items")
    print(f"         Found {len(sessions)} session(s).")
    if sessions:
        print(f"         Fields: {list(sessions[0].keys())}")
    return sessions


def get_test_id(session):
    """
    Extract the LambdaTest test ID (e.g. M1EH6-3AAHV-3JGGN-YAARK) from a
    HyEx session object. The field may vary — check common names.
    """
    for key in ("testID", "test_id", "testId", "sessionID", "session_id", "sessionId"):
        val = session.get(key)
        if val and isinstance(val, str) and len(str(val)) > 5:
            return str(val)
        if val and isinstance(val, int):
            # integer sessionID is the HyEx internal ID, not the LT test ID — skip
            continue
    return None


# ── Step 2: Fetch session detail → all URLs ───────────────────────────────────
def fetch_session_detail(test_id):
    """
    GET /sessions/{test_id} returns a rich object with pre-built URLs for
    video, screenshots zip, and all log types.

    Routes to:
      - Real Device: https://mobile-api.lambdatest.com/mobile-automation/api/v1
      - Web/Selenium: https://api.lambdatest.com/automation/api/v1
    """
    base = get_base_url(test_id)
    url  = f"{base}/sessions/{test_id}"
    print(f"           API type : {session_type(test_id)}")
    print(f"           Endpoint : {url}")
    resp = get(url)
    if not resp:
        return None
    return resp.get("data") or resp


# ── Step 3: Download all artifacts for a session ──────────────────────────────
def download_session_artifacts(test_id, detail, idx):
    out_dir = BASE_DIR / f"session_{idx:03d}_{test_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {"downloaded": [], "skipped": []}

    # ── Video ──────────────────────────────────────────────────────────────────
    video_url = detail.get("video_url")
    if video_url:
        download_binary(video_url, out_dir / "video.mp4", "Video")
        summary["downloaded"].append("video")
    else:
        print(f"    – No video URL in session detail.")
        summary["skipped"].append("video")

    # ── Screenshots (bundled zip) ──────────────────────────────────────────────
    screenshot_url = detail.get("screenshot_url")
    if screenshot_url:
        download_binary(screenshot_url, out_dir / "screenshots.zip", "Screenshots ZIP")
        summary["downloaded"].append("screenshots")
    else:
        print(f"    – No screenshot URL in session detail.")
        summary["skipped"].append("screenshots")

    # ── Logs ───────────────────────────────────────────────────────────────────
    log_fields = get_log_fields(test_id)
    for url_field, filename in log_fields:
        log_url = detail.get(url_field)
        if log_url:
            ok = download_log(log_url, out_dir / filename, url_field.replace("_logs_url", "").replace("_url", ""))
            summary["downloaded" if ok else "skipped"].append(filename)
        else:
            print(f"    – No URL for {url_field} (may not be available for this session).")
            summary["skipped"].append(filename)

    return summary


# ── Summary ───────────────────────────────────────────────────────────────────
def write_summary(results):
    out = BASE_DIR / "artifact_summary.json"
    out.write_text(json.dumps({
        "job_id"  : JOB_ID,
        "sessions": len(results),
        "results" : results,
    }, indent=2))
    print(f"\n[Summary] → {out}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f" HyperExecute Artifact Downloader")
    if _RAW_JOB_NUMBER:
        print(f" Job Number : {_RAW_JOB_NUMBER}  →  {JOB_ID}")
    else:
        print(f" Job ID     : {JOB_ID}")
    print(f" Output dir : {BASE_DIR.resolve()}")
    print("=" * 60)

    sessions = fetch_sessions(JOB_ID)
    if not sessions:
        print("[ERROR] No sessions found. Check JOB_ID and credentials.")
        sys.exit(1)

    results = []
    for idx, session in enumerate(sessions, start=1):
        print(f"\n{'='*60}")
        print(f"[Session {idx}/{len(sessions)}]")

        test_id = get_test_id(session)
        if not test_id:
            print(f"  [SKIP] Could not find test ID in session: {session}")
            results.append({"error": "no test_id", "session": session})
            continue

        print(f"  Test ID : {test_id}")

        # Step 2 – get session detail with all URLs
        print(f"  [Step 2] Fetching session detail …")
        detail = fetch_session_detail(test_id)
        if not detail:
            print(f"  [SKIP] Session detail not returned for {test_id}")
            results.append({"test_id": test_id, "error": "no detail returned"})
            continue

        # Print key fields for visibility
        print(f"           Name     : {detail.get('name', '-')}")
        print(f"           Status   : {detail.get('status_ind', '-')}")
        print(f"           Browser  : {detail.get('browser', '-')} {detail.get('browser_version', '')}")
        print(f"           Platform : {detail.get('platform', '-')}")

        # Step 3 – download everything
        print(f"  [Step 3] Downloading artifacts …")
        dl_summary = download_session_artifacts(test_id, detail, idx)

        results.append({
            "test_id"    : test_id,
            "name"       : detail.get("name"),
            "status"     : detail.get("status_ind"),
            "downloaded" : dl_summary["downloaded"],
            "skipped"    : dl_summary["skipped"],
        })

    write_summary(results)

    print(f"\n{'='*60}")
    print(f"✅  Done → {BASE_DIR.resolve()}")
    print(f"{'='*60}")
    print(f"\nStructure:")
    print(f"  {JOB_ID}/")
    print(f"  ├── session_001_<test_id>/")
    print(f"  │   ├── video.mp4")
    print(f"  │   ├── screenshots.zip")
    print(f"  │   ├── selenium.log")
    print(f"  │   ├── console.log")
    print(f"  │   ├── network.log")
    print(f"  │   └── command.log")
    print(f"  ├── session_002_<test_id>/")
    print(f"  └── artifact_summary.json")
    print(f"")
    print(f"Log files per session type:")
    print(f"  Web/Selenium : selenium.log, console.log, network.log, command.log")
    print(f"  Real Device  : appium.log, device.log, crash.log, network.log, console.log")


if __name__ == "__main__":
    main()