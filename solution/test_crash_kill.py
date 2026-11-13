"""Test crash recovery by killing the dispatcher with SIGTERM/kill mid-run and resuming."""
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


def run_kill_recovery_test(speed: float = 1200.0, seed: int = 42, failure_scale: float = 1.0):
    print(f"\n" + "=" * 65)
    print(f"  RUNNING CRASH RECOVERY TEST (kill -9 simulation)")
    print(f"  Seed: {seed} | Failure Scale: {failure_scale}")
    print(f"=" * 65)

    free_ports(8099, 8200)
    time.sleep(1.0)

    db_file = os.path.join(ROOT_DIR, "solution", "dispatcher.db")
    out_file = os.path.join(ROOT_DIR, "solution", "final_state.csv")
    for f in (db_file, out_file, f"{db_file}-wal", f"{db_file}-shm"):
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

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
    mock_proc = subprocess.Popen(mock_cmd, cwd=mock_cwd)

    try:
        if not wait_for_server("http://127.0.0.1:8099/v1/clock"):
            print("ERROR: Failed to connect to carrier clock!")
            return False

        disp_cmd = [
            sys.executable,
            "-m",
            "solution",
            "--db-path",
            db_file,
            "--output",
            out_file,
        ]

        print("[Phase 1] Starting dispatcher process...")
        proc1 = subprocess.Popen(disp_cmd, cwd=ROOT_DIR)

        # Run for 10 seconds while active calls are placed
        time.sleep(10.0)

        print("\n>>> SIMULATING HARD CRASH: kill -9 on Dispatcher Process <<<")
        proc1.kill()
        proc1.wait()
        print("Dispatcher killed! Waiting 3 seconds while carrier state evolves...")
        time.sleep(3.0)

        print("[Phase 2] Restarting dispatcher from persistent SQLite database...")
        start_time = time.time()
        proc2 = subprocess.Popen(disp_cmd, cwd=ROOT_DIR)

        ret2 = proc2.wait(timeout=400)
        elapsed = time.time() - start_time
        print(f"\nResumed dispatcher completed in {elapsed:.1f}s with code {ret2}!")

        # Verify admin report
        report_resp = httpx.get("http://127.0.0.1:8099/admin/report", timeout=5.0)
        report = report_resp.json()
        print("\n--- Carrier Admin Report After Crash & Resume ---")
        print(json.dumps(report, indent=2))

        leads_resp = httpx.get("http://127.0.0.1:8099/admin/leads", timeout=5.0)
        expected_leads = leads_resp.json()

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
        print(f"  CRASH RECOVERY RESULT: {'PASS (0 Violations, 0 Mismatches)' if success else 'FAIL'}")
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
    run_kill_recovery_test()
