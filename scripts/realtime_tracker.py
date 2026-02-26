#!/usr/bin/env python3
"""
HIL Test Automation - Realtime Per-Test Pass/Fail Tracker
==========================================================

Cung cấp realtime tracking cho từng test script đang chạy trên mỗi bench.
Mỗi bench ghi 1 file JSON riêng → Jenkins poll + aggregate → hiển thị dashboard.

Architecture:
    test_runner_wrapper.py
        ↓ (gọi TestTracker.start_test / finish_test mỗi khi parse được kết quả)
    tracker_{bench}.json  (1 file per bench, update mỗi test)
        ↓ (Jenkins parallel step poll mỗi 30s)
    TrackerAggregator.aggregate()
        ↓
    combined_status.json + console summary
        ↓
    Jenkins console output (realtime visible)

Usage:
    # Programmatic (from test_runner_wrapper.py):
    from realtime_tracker import TestTracker
    tracker = TestTracker("results/tracker_bench1.json", bench="hil-bench-1")
    tracker.start_test("TC001", "TC001_BasicEcall", feature="eCall")
    tracker.finish_test("TC001", status="PASS", duration=12.5)
    tracker.save()

    # CLI - Poll and print aggregated status:
    python realtime_tracker.py poll --dir results/ --interval 30

    # CLI - One-shot aggregate:
    python realtime_tracker.py aggregate --dir results/ --output combined.json

    # CLI - Print summary table:
    python realtime_tracker.py summary --dir results/

Requirements:
    Python 3.7+ (standard library only)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


# =============================================================================
# TEST TRACKER (per-bench, written by test_runner_wrapper.py)
# =============================================================================

class TestTracker:
    """
    Append-only per-test tracker.
    Writes a JSON status file that gets updated after every test completes.
    Thread-safe via file-level atomicity (write-to-temp + rename).
    """

    def __init__(self, tracker_file: str, bench: str = "unknown",
                 feature: str = "unknown", variant: str = "unknown"):
        self.tracker_file = Path(tracker_file)
        self.bench = bench
        self.feature = feature
        self.variant = variant
        self.start_time = datetime.now().isoformat()

        # Test tracking state
        self._tests: Dict[str, dict] = {}  # tcid → test entry
        self._test_order: List[str] = []    # preserve order
        self._counters = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "running": 0,
            "hung": 0,
        }
        self._current_test: Optional[str] = None

        # Create parent directory
        self.tracker_file.parent.mkdir(parents=True, exist_ok=True)

        # Load existing state if file exists (resume support)
        if self.tracker_file.exists():
            try:
                with open(self.tracker_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if 'tests' in data:
                        for test in data['tests']:
                            tid = test.get('tcid', test.get('name', ''))
                            self._tests[tid] = test
                            if tid not in self._test_order:
                                self._test_order.append(tid)
                        self._recalculate_counters()
            except (json.JSONDecodeError, KeyError):
                pass  # Start fresh

    def _recalculate_counters(self):
        """Recalculate counters from test entries."""
        self._counters = {
            "total": len(self._tests),
            "passed": 0, "failed": 0, "skipped": 0, "running": 0, "hung": 0,
        }
        for test in self._tests.values():
            status = test.get('status', 'UNKNOWN')
            if status == 'PASS':
                self._counters['passed'] += 1
            elif status == 'FAIL':
                self._counters['failed'] += 1
            elif status == 'SKIP':
                self._counters['skipped'] += 1
            elif status == 'RUNNING':
                self._counters['running'] += 1
            elif status in ('HANG', 'TIMEOUT'):
                self._counters['hung'] += 1

    def start_test(self, tcid: str, name: str = "", feature: str = ""):
        """Mark a test as started/running."""
        resolved_feature = feature or self.feature
        entry = {
            "tcid": tcid,
            "name": name or tcid,
            "bench": self.bench,
            "feature": resolved_feature,
            "variant": self.variant,
            "status": "RUNNING",
            "start_time": datetime.now().isoformat(),
            "end_time": None,
            "duration_sec": 0.0,
            "error_msg": "",
        }

        # If previously recorded (retry), keep previous data as sub-entry
        if tcid in self._tests:
            prev = self._tests[tcid]
            if prev.get('status') == 'RUNNING':
                # Was running but didn't finish → probable hang in previous attempt
                prev['status'] = 'HANG'
                prev['end_time'] = datetime.now().isoformat()

        self._tests[tcid] = entry
        if tcid not in self._test_order:
            self._test_order.append(tcid)

        self._current_test = tcid
        self._recalculate_counters()
        self.save()

    def finish_test(self, tcid: str, status: str = "PASS",
                    duration: float = 0.0, error_msg: str = ""):
        """Mark a test as completed with pass/fail status."""
        if tcid in self._tests:
            entry = self._tests[tcid]
            entry['status'] = status.upper()
            entry['end_time'] = datetime.now().isoformat()
            entry['duration_sec'] = round(duration, 2)
            entry['error_msg'] = error_msg[:500] if error_msg else ""
        else:
            # Test wasn't started (edge case) — create completed entry
            self._tests[tcid] = {
                "tcid": tcid,
                "name": tcid,
                "bench": self.bench,
                "feature": self.feature,
                "variant": self.variant,
                "status": status.upper(),
                "start_time": datetime.now().isoformat(),
                "end_time": datetime.now().isoformat(),
                "duration_sec": round(duration, 2),
                "error_msg": error_msg[:500] if error_msg else "",
            }
            if tcid not in self._test_order:
                self._test_order.append(tcid)

        if self._current_test == tcid:
            self._current_test = None

        self._recalculate_counters()
        self.save()

    def mark_hang(self, tcid: str):
        """Mark the current/specified test as hung."""
        target = tcid or self._current_test
        if target and target in self._tests:
            self._tests[target]['status'] = 'HANG'
            self._tests[target]['end_time'] = datetime.now().isoformat()
            self._tests[target]['error_msg'] = "HANG DETECTED — no output, process killed"
            self._current_test = None
            self._recalculate_counters()
            self.save()

    def save(self):
        """Write tracker state to JSON file (atomic write via temp file)."""
        # Build ordered test list
        ordered_tests = []
        for tcid in self._test_order:
            if tcid in self._tests:
                ordered_tests.append(self._tests[tcid])

        data = {
            "bench": self.bench,
            "feature": self.feature,
            "variant": self.variant,
            "start_time": self.start_time,
            "last_updated": datetime.now().isoformat(),
            "counters": self._counters.copy(),
            "current_test": self._current_test,
            "tests": ordered_tests,
        }

        # Atomic write: write to temp, then rename
        tmp_path = self.tracker_file.with_suffix('.tmp')
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            # On Windows, need to remove target first if exists
            if self.tracker_file.exists():
                os.replace(str(tmp_path), str(self.tracker_file))
            else:
                tmp_path.rename(self.tracker_file)
        except Exception as e:
            # Fallback: direct write
            try:
                with open(self.tracker_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except Exception:
                print(f"⚠️ Tracker save failed: {e}", file=sys.stderr)

    def get_summary_line(self) -> str:
        """Get a one-line summary string."""
        c = self._counters
        total = c['total']
        if total == 0:
            return f"[{self.bench}] No tests recorded yet"

        pass_rate = round(c['passed'] / max(total, 1) * 100)
        parts = [f"✅{c['passed']}"]
        if c['failed']:
            parts.append(f"❌{c['failed']}")
        if c['running']:
            parts.append(f"🔄{c['running']}")
        if c['hung']:
            parts.append(f"🔴{c['hung']}")
        if c['skipped']:
            parts.append(f"⏭️{c['skipped']}")

        return (f"[{self.bench}] {'/'.join(parts)} "
                f"({c['passed']}/{total} = {pass_rate}%)")


# =============================================================================
# TRACKER AGGREGATOR (merges multiple bench trackers)
# =============================================================================

class TrackerAggregator:
    """
    Aggregate multiple bench tracker JSON files into a combined view.
    Used by Jenkins polling step.
    """

    @staticmethod
    def find_tracker_files(results_dir: str) -> List[Path]:
        """Find all tracker_*.json files in results directory."""
        results_path = Path(results_dir)
        if not results_path.exists():
            return []
        return sorted(results_path.rglob('tracker_*.json'))

    @staticmethod
    def load_tracker(tracker_file: Path) -> Optional[dict]:
        """Load a single tracker JSON file safely."""
        try:
            with open(tracker_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    @classmethod
    def aggregate(cls, results_dir: str) -> dict:
        """Aggregate all bench tracker files into a combined status."""
        tracker_files = cls.find_tracker_files(results_dir)

        combined = {
            "timestamp": datetime.now().isoformat(),
            "bench_count": 0,
            "counters": {
                "total": 0, "passed": 0, "failed": 0,
                "skipped": 0, "running": 0, "hung": 0,
            },
            "benches": {},
            "all_tests": [],
        }

        for tf in tracker_files:
            data = cls.load_tracker(tf)
            if not data:
                continue

            bench = data.get('bench', tf.stem)
            counters = data.get('counters', {})
            combined['bench_count'] += 1

            # Aggregate counters
            for key in combined['counters']:
                combined['counters'][key] += counters.get(key, 0)

            # Per-bench summary
            combined['benches'][bench] = {
                "file": str(tf),
                "counters": counters,
                "current_test": data.get('current_test'),
                "last_updated": data.get('last_updated'),
            }

            # Collect all tests
            for test in data.get('tests', []):
                combined['all_tests'].append(test)

        # Calculate overall pass rate
        total = combined['counters']['total']
        passed = combined['counters']['passed']
        combined['pass_rate'] = round(passed / max(total, 1) * 100, 1)

        return combined

    @classmethod
    def print_summary(cls, results_dir: str, show_failures: bool = True):
        """Print a formatted console summary."""
        combined = cls.aggregate(results_dir)
        c = combined['counters']
        total = c['total']

        if total == 0:
            print("📊 No test results yet...")
            return

        # Header
        print(f"\n{'═' * 72}")
        print(f"📊 REALTIME TEST STATUS  |  {datetime.now().strftime('%H:%M:%S')}  "
              f"|  {combined['bench_count']} bench(es)")
        print(f"{'═' * 72}")

        # Overall stats
        pass_rate = combined['pass_rate']
        emoji = '✅' if pass_rate >= 85 else ('⚠️' if pass_rate >= 60 else '❌')
        bar_len = 40
        filled = int(pass_rate / 100 * bar_len)
        bar = '█' * filled + '░' * (bar_len - filled)

        print(f"  {emoji} Overall: {bar} {pass_rate}% "
              f"({c['passed']}/{total})")
        print(f"     Passed: {c['passed']}  |  Failed: {c['failed']}  "
              f"|  Running: {c['running']}  |  Hung: {c['hung']}  "
              f"|  Skipped: {c['skipped']}")

        # Per-bench status
        print(f"\n{'─' * 72}")
        print(f"  {'Bench':<20s} {'Pass':>5s} {'Fail':>5s} {'Run':>5s} "
              f"{'Total':>6s} {'Rate':>6s}  Current Test")
        print(f"{'─' * 72}")

        for bench, info in sorted(combined['benches'].items()):
            bc = info['counters']
            bt = bc.get('total', 0)
            bp = bc.get('passed', 0)
            bf = bc.get('failed', 0)
            br = bc.get('running', 0)
            rate = f"{round(bp / max(bt, 1) * 100)}%" if bt > 0 else "-"
            current = info.get('current_test') or '-'
            if len(current) > 25:
                current = current[:22] + '...'

            print(f"  {bench:<20s} {bp:>5d} {bf:>5d} {br:>5d} "
                  f"{bt:>6d} {rate:>6s}  {current}")

        # Recent failures (last 10)
        if show_failures:
            failures = [t for t in combined['all_tests']
                        if t.get('status') in ('FAIL', 'HANG', 'TIMEOUT')]
            if failures:
                recent = failures[-10:]
                print(f"\n{'─' * 72}")
                print(f"  ❌ Recent Failures ({len(failures)} total, "
                      f"showing last {len(recent)}):")
                for t in recent:
                    bench = t.get('bench', '?')[:12]
                    name = t.get('name', t.get('tcid', '?'))
                    if len(name) > 40:
                        name = name[:37] + '...'
                    status = t.get('status', '?')
                    err = t.get('error_msg', '')[:60]
                    print(f"    [{bench}] {status}: {name}")
                    if err:
                        print(f"             └─ {err}")

        print(f"{'═' * 72}\n")


# =============================================================================
# CLI COMMANDS
# =============================================================================

def cmd_poll(args):
    """Poll tracker files and print summary at regular intervals."""
    interval = int(args.interval)
    results_dir = args.dir

    print(f"🔄 Polling tracker files in: {results_dir} (every {interval}s)")
    print(f"   Press Ctrl+C to stop.\n")

    iteration = 0
    try:
        while True:
            iteration += 1
            TrackerAggregator.print_summary(results_dir,
                                            show_failures=(iteration % 3 == 1))
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n⏹️ Polling stopped.")


def cmd_aggregate(args):
    """Aggregate all tracker files and output combined JSON."""
    combined = TrackerAggregator.aggregate(args.dir)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(combined, f, indent=2, ensure_ascii=False)
        print(f"✅ Combined status written to: {output_path}")
        print(f"   {combined['counters']['total']} tests across "
              f"{combined['bench_count']} benches "
              f"({combined['pass_rate']}% pass rate)")
    else:
        print(json.dumps(combined, indent=2, ensure_ascii=False))


def cmd_summary(args):
    """Print a one-shot summary table."""
    TrackerAggregator.print_summary(args.dir, show_failures=True)


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="HIL Realtime Test Tracker - Per-test pass/fail tracking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Poll and print live summary every 30s
  python realtime_tracker.py poll --dir results/ --interval 30

  # One-shot aggregate to JSON
  python realtime_tracker.py aggregate --dir results/ --output combined.json

  # Print summary table
  python realtime_tracker.py summary --dir results/
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── poll ──
    p_poll = subparsers.add_parser("poll",
        help="Poll tracker files periodically and print summary")
    p_poll.add_argument("--dir", required=True,
        help="Results directory containing tracker_*.json files")
    p_poll.add_argument("--interval", default="30",
        help="Polling interval in seconds (default: 30)")

    # ── aggregate ──
    p_agg = subparsers.add_parser("aggregate",
        help="Aggregate all tracker files into combined JSON")
    p_agg.add_argument("--dir", required=True,
        help="Results directory")
    p_agg.add_argument("--output", default=None,
        help="Output file (default: stdout)")

    # ── summary ──
    p_sum = subparsers.add_parser("summary",
        help="Print one-shot summary table")
    p_sum.add_argument("--dir", required=True,
        help="Results directory")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "poll": cmd_poll,
        "aggregate": cmd_aggregate,
        "summary": cmd_summary,
    }

    try:
        commands[args.command](args)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
