"""Validation script to run mock telephony + dispatcher and verify 0 violations."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time

import httpx

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def free_ports(*ports: int) -> None:
    """Kill any process holding the specified ports on Windows or POSIX."""
    for port in ports:
        try:
            if sys.platform == "win32":
                cmd = f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"
                subprocess.run(["powershell", "-Command", cmd], capture_output=True, timeout=5)
            else:
                subprocess.run(f"lsof -ti:{port} | xargs kill -9 2>/dev/null", shell=True, timeout=5)
        except Exception:
            pass


def wait_for_server(url: str, timeout: float = 12.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def run_clean_test(speed: float = 1200.0, seed: int = 7, failure_scale: float = 1.0):
    print(f"\n" + "=" * 65)
    print(f"  RUNNING END-TO-END VALIDATION TEST")
    print(f"  Speed: {speed}x | Seed: {seed} | Failure Scale: {failure_scale}")
    print(f"=" * 65)

    free_ports(8099, 8200)
    time.sleep(1.0)

    # Clean previous run artifacts
    db_file = os.path.join(ROOT_DIR, "solution", "dispatcher.db")
    out_file = os.path.join(ROOT_DIR, "solution", "final_state.csv")
    for f in (db_file, out_file, f"{db_file}-wal", f"{db_file}-shm"):
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

    # Start mock telephony from assignment-b-workflow
    mock_cwd = os.path.join(ROOT_DIR, "assignment-b-workflow")
    mock_cmd = [
        sys.executable,
        "-m",
        "mock_telephony",
        "--speed",
        str(speed),
        "--seed",
        str(seed),
        "--failure-scale",
        str(failure_scale),
    ]
    print(f"[1/3] Starting carrier mock API on http://127.0.0.1:8099 ...")
    mock_proc = subprocess.Popen(mock_cmd, cwd=mock_cwd)

    try:
        if not wait_for_server("http://127.0.0.1:8099/v1/clock"):
            print("ERROR: Failed to connect to mock telephony clock!")
            return False

        print("[2/3] Carrier is live! Launching durable dispatcher...")
        disp_cmd = [
            sys.executable,
            "-m",
            "solution",
            "--db-path",
            db_file,
            "--output",
            out_file,
        ]
        start_time = time.time()
        disp_proc = subprocess.Popen(disp_cmd, cwd=ROOT_DIR)

        ret = disp_proc.wait(timeout=400)
        elapsed = time.time() - start_time
        print(f"\n[3/3] Dispatcher completed in {elapsed:.1f}s with exit code {ret}!")

        # Fetch admin report from carrier
        report_resp = httpx.get("http://127.0.0.1:8099/admin/report", timeout=5.0)
        report = report_resp.json()
        print("\n--- Telephony Carrier Admin Report ---")
        print(json.dumps(report, indent=2))

        # Fetch expected leads from carrier
        leads_resp = httpx.get("http://127.0.0.1:8099/admin/leads", timeout=5.0)
        expected_leads = leads_resp.json()

        if not os.path.exists(out_file):
            print(f"ERROR: {out_file} was not generated!")
            return False

        with open(out_file, "r", encoding="utf-8") as f:
            actual_leads = list(csv.DictReader(f))

        mismatches = 0
        for actual, expected in zip(actual_leads, expected_leads):
            if not (
                actual["campaign_id"] == expected["campaign_id"]
                and actual["lead_id"] == expected["lead_id"]
                and actual["final_state"] == expected["expected_state"]
                and int(actual["attempts"]) == int(expected["attempts"])
                and (actual["last_status"] or "") == (expected["last_status"] or "")
                and (actual["last_disposition"] or "") == (expected["last_disposition"] or "")
            ):
                mismatches += 1

        print("\n" + "=" * 65)
        print(f"  Total Leads Evaluated: {len(actual_leads)} / {len(expected_leads)}")
        print(f"  Total Mismatches:      {mismatches}")
        print(f"  Total Violations:      {report['violation_count']}")
        success = (mismatches == 0) and (report["violation_count"] == 0)
        print(f"  FINAL RESULT:          {'PASS (0 Violations, 0 Mismatches)' if success else 'FAIL'}")
        print("=" * 65 + "\n")
        return success

    finally:
        mock_proc.terminate()
        try:
            mock_proc.wait(timeout=3)
        except Exception:
            mock_proc.kill()
        free_ports(8099, 8200)


if __name__ == "__main__":
    speed = float(sys.argv[1]) if len(sys.argv) > 1 else 1200.0
    failure = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    run_clean_test(speed=speed, seed=seed, failure_scale=failure)
