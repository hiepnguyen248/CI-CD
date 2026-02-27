#!/usr/bin/env python3
"""
HIL Test Automation - Robot Framework Execution Wrapper with Hang Detection
============================================================================

Giải quyết vấn đề: Robot Framework bị treo (hang) do HW/SW issues trên bench.

Cơ chế hoạt động:
1. Chạy robot command trong subprocess với timeout per-test
2. Monitor output - nếu không có output mới trong X giây → test đã hang
3. Kill process, đánh dấu test đang chạy là TIMEOUT/HANG
4. Tự động chạy tiếp các test cases còn lại (skip test bị hang)
5. Merge tất cả kết quả thành 1 output.xml duy nhất

Usage:
    python test_runner_wrapper.py --suite tests/HVAC --outputdir results/HVAC ^
        --hang-timeout 300 --test-timeout 600 -- --variable VARIANT:A --loglevel DEBUG

    Tất cả arguments sau "--" sẽ được pass thẳng cho robot command.

Requirements:
    pip install robotframework psutil
"""

import argparse
import glob
import os
import signal
import subprocess
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

try:
    import psutil
except ImportError:
    psutil = None
    print("WARNING: psutil not installed. Process tree kill may not work. Run: pip install psutil")

# Realtime tracker (optional, degrades gracefully)
try:
    from realtime_tracker import TestTracker
except ImportError:
    TestTracker = None


# =============================================================================
# CONSTANTS
# =============================================================================

DEFAULT_HANG_TIMEOUT = 300      # 5 minutes - no output → hang detected
DEFAULT_TEST_TIMEOUT = 600      # 10 minutes - max time per single test
DEFAULT_SUITE_TIMEOUT = 7200    # 2 hours - max time per suite/feature run
HEALTH_CHECK_INTERVAL = 10      # Check every 10 seconds

# Early Abort (Fail-Fast) defaults
DEFAULT_MAX_CONSECUTIVE_FAILS = 40  # 40 consecutive fails → abort
DEFAULT_FAIL_RATE_ABORT = 80        # >80% fail after min tests → abort
DEFAULT_MIN_TESTS_FOR_RATE = 70     # Min tests before checking fail rate


# =============================================================================
# PROCESS MANAGEMENT
# =============================================================================

def kill_process_tree(pid: int):
    """Kill a process and all its children (important for robot + CANoe etc.)."""
    if psutil:
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            parent.kill()
            # Wait for processes to actually terminate
            gone, alive = psutil.wait_procs(children + [parent], timeout=10)
            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass
    else:
        # Fallback without psutil (Windows)
        try:
            subprocess.run(
                f'taskkill /PID {pid} /T /F',
                shell=True, capture_output=True, timeout=15
            )
        except Exception:
            pass


class OutputMonitor:
    """
    Monitor subprocess output for hang detection + early abort.
    Tracks the last time new output was received.
    Optionally emits realtime events to TestTracker.
    """

    def __init__(self, tracker: 'TestTracker' = None,
                 max_consecutive_fails: int = DEFAULT_MAX_CONSECUTIVE_FAILS,
                 fail_rate_abort: int = DEFAULT_FAIL_RATE_ABORT,
                 min_tests_for_rate: int = DEFAULT_MIN_TESTS_FOR_RATE):
        self.last_output_time = time.time()
        self.output_lines: List[str] = []
        self.current_test: Optional[str] = None
        self.lock = threading.Lock()
        self._stop = False
        self._tracker = tracker
        self._test_start_time: Optional[float] = None

        # Early abort tracking
        self._max_consecutive_fails = max_consecutive_fails
        self._fail_rate_abort = fail_rate_abort
        self._min_tests_for_rate = min_tests_for_rate
        self._consecutive_fails = 0
        self._total_pass = 0
        self._total_fail = 0
        self._abort_triggered = False
        self._abort_reason = ""

    def update(self, line: str):
        with self.lock:
            self.last_output_time = time.time()
            self.output_lines.append(line)

            # Track which test is currently running (from robot output)
            # Robot Framework outputs lines like: "test_name  | PASS |" or
            # progress markers during execution
            line_stripped = line.strip()
            if '| FAIL |' in line_stripped or '| PASS |' in line_stripped:
                # Test completed — extract name and status
                parts = line_stripped.split('|')
                if len(parts) >= 2:
                    test_name = parts[0].strip()
                    status = 'PASS' if '| PASS |' in line_stripped else 'FAIL'
                    # Extract error message for failures
                    error_msg = ''
                    if status == 'FAIL' and len(parts) >= 3:
                        error_msg = parts[2].strip() if len(parts) > 2 else ''

                    # Calculate duration
                    duration = 0.0
                    if self._test_start_time:
                        duration = time.time() - self._test_start_time
                        self._test_start_time = None

                    # ─── Early Abort: track pass/fail stats ───
                    if status == 'FAIL':
                        self._consecutive_fails += 1
                        self._total_fail += 1
                    else:
                        self._consecutive_fails = 0
                        self._total_pass += 1
                    self._check_abort_conditions(test_name)

                    # Emit to realtime tracker
                    if self._tracker:
                        try:
                            self._tracker.finish_test(
                                tcid=test_name,
                                status=status,
                                duration=duration,
                                error_msg=error_msg,
                            )
                        except Exception:
                            pass  # Never let tracker errors break execution

                    self.current_test = test_name
            elif line_stripped and not line_stripped.startswith('='):
                # Could be a test starting
                if '::' in line_stripped or line_stripped.startswith('test_') or line_stripped.startswith('TC_'):
                    test_id = line_stripped.split('::')[-1].strip().split()[0] if '::' in line_stripped else line_stripped.split()[0]
                    self.current_test = test_id
                    self._test_start_time = time.time()

                    # Emit to realtime tracker
                    if self._tracker:
                        try:
                            self._tracker.start_test(
                                tcid=test_id,
                                name=test_id,
                            )
                        except Exception:
                            pass

    def _check_abort_conditions(self, last_test: str):
        """Check if early abort should be triggered."""
        total = self._total_pass + self._total_fail

        # Rule 1: consecutive fails threshold
        if (self._max_consecutive_fails > 0 and
                self._consecutive_fails >= self._max_consecutive_fails):
            self._abort_triggered = True
            self._abort_reason = (
                f"EARLY ABORT: {self._consecutive_fails} consecutive FAILs "
                f"(threshold: {self._max_consecutive_fails}). "
                f"Likely environment issue — stopping to save time."
            )
            return

        # Rule 2: fail rate threshold (only after min tests)
        if (self._fail_rate_abort > 0 and
                total >= self._min_tests_for_rate):
            fail_rate = (self._total_fail / total) * 100
            if fail_rate >= self._fail_rate_abort:
                self._abort_triggered = True
                self._abort_reason = (
                    f"EARLY ABORT: Fail rate {fail_rate:.0f}% "
                    f"({self._total_fail}/{total} tests) exceeds "
                    f"{self._fail_rate_abort}% threshold. "
                    f"Stopping to save time."
                )

    def should_abort(self) -> bool:
        with self.lock:
            return self._abort_triggered

    def get_abort_reason(self) -> str:
        with self.lock:
            return self._abort_reason

    def get_idle_seconds(self) -> float:
        with self.lock:
            return time.time() - self.last_output_time

    def get_current_test(self) -> Optional[str]:
        with self.lock:
            return self.current_test

    def get_stats(self) -> dict:
        with self.lock:
            total = self._total_pass + self._total_fail
            return {
                'total': total,
                'passed': self._total_pass,
                'failed': self._total_fail,
                'consecutive_fails': self._consecutive_fails,
                'fail_rate': round((self._total_fail / max(total, 1)) * 100, 1),
            }

    def stop(self):
        self._stop = True


def stream_process_output(process: subprocess.Popen, monitor: OutputMonitor, log_file=None):
    """Read process stdout/stderr in a separate thread."""
    try:
        for line in iter(process.stdout.readline, ''):
            if monitor._stop:
                break
            line = line.rstrip('\n\r')
            monitor.update(line)
            print(line, flush=True)  # Echo to console
            if log_file:
                log_file.write(line + '\n')
                log_file.flush()
    except (ValueError, OSError):
        pass  # Process closed


# =============================================================================
# ROBOT FRAMEWORK EXECUTION WITH HANG DETECTION
# =============================================================================

def run_robot_with_hang_detection(
    robot_args: List[str],
    outputdir: str,
    hang_timeout: int = DEFAULT_HANG_TIMEOUT,
    test_timeout: int = DEFAULT_TEST_TIMEOUT,
    suite_timeout: int = DEFAULT_SUITE_TIMEOUT,
    run_label: str = "main",
    tracker: 'TestTracker' = None,
    max_consecutive_fails: int = DEFAULT_MAX_CONSECUTIVE_FAILS,
    fail_rate_abort: int = DEFAULT_FAIL_RATE_ABORT,
) -> dict:
    """
    Run Robot Framework with hang detection.

    Returns dict with:
        - return_code: robot exit code (0=all pass, 1=failures, -1=hang killed)
        - hang_detected: bool
        - hung_test: name of test that was hanging (if any)
        - output_xml: path to output.xml
        - duration_sec: total execution time
    """
    os.makedirs(outputdir, exist_ok=True)

    # Build robot command with test-level timeout
    cmd = ['robot', f'--timeout', f'{test_timeout}s']
    cmd.extend(robot_args)

    # Ensure outputdir is set
    has_outputdir = False
    for arg in robot_args:
        if '--outputdir' in arg:
            has_outputdir = True
            break
    if not has_outputdir:
        cmd.extend(['--outputdir', outputdir])

    cmd_str = ' '.join(cmd)
    print(f"\n{'='*70}")
    print(f"🤖 Robot Wrapper: Starting execution")
    print(f"   Command: {cmd_str}")
    print(f"   Hang timeout: {hang_timeout}s (no output)")
    print(f"   Test timeout: {test_timeout}s (per test)")
    print(f"   Suite timeout: {suite_timeout}s (total)")
    print(f"{'='*70}\n")

    start_time = time.time()
    monitor = OutputMonitor(
        tracker=tracker,
        max_consecutive_fails=max_consecutive_fails,
        fail_rate_abort=fail_rate_abort,
    )
    result = {
        'return_code': 0,
        'hang_detected': False,
        'hung_test': None,
        'output_xml': os.path.join(outputdir, 'output.xml'),
        'duration_sec': 0,
        'killed': False,
        'early_abort': False,
        'abort_reason': '',
    }

    # Log file for debugging
    log_path = os.path.join(outputdir, f'wrapper_{run_label}.log')
    log_file = open(log_path, 'w', encoding='utf-8')

    try:
        # Start robot process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line buffered
            encoding='utf-8',
            errors='replace',
        )

        # Start output reader thread
        reader_thread = threading.Thread(
            target=stream_process_output,
            args=(process, monitor, log_file),
            daemon=True
        )
        reader_thread.start()

        # Monitor loop - check for hangs
        while process.poll() is None:
            time.sleep(HEALTH_CHECK_INTERVAL)

            elapsed = time.time() - start_time
            idle = monitor.get_idle_seconds()

            # Check suite timeout
            if elapsed > suite_timeout:
                current_test = monitor.get_current_test()
                print(f"\n🔴 SUITE TIMEOUT after {elapsed:.0f}s!")
                print(f"   Total suite timeout ({suite_timeout}s) exceeded.")
                print(f"   Last active test: {current_test}")
                log_file.write(f"\n[WRAPPER] Suite timeout after {elapsed:.0f}s\n")

                kill_process_tree(process.pid)
                result['return_code'] = -1
                result['hang_detected'] = True
                result['hung_test'] = current_test
                result['killed'] = True
                break

            # Check hang (no output)
            if idle > hang_timeout:
                current_test = monitor.get_current_test()
                print(f"\n🔴 HANG DETECTED! No output for {idle:.0f}s (threshold: {hang_timeout}s)")
                print(f"   Suspected hung test: {current_test}")
                print(f"   Killing robot process (PID: {process.pid})...")
                log_file.write(f"\n[WRAPPER] HANG DETECTED after {idle:.0f}s idle. Killing PID {process.pid}\n")
                log_file.write(f"[WRAPPER] Suspected hung test: {current_test}\n")

                # Mark hung test in tracker
                if tracker and current_test:
                    try:
                        tracker.mark_hang(current_test)
                    except Exception:
                        pass

                kill_process_tree(process.pid)
                result['return_code'] = -1
                result['hang_detected'] = True
                result['hung_test'] = current_test
                result['killed'] = True
                break

            # Check early abort (mass failures)
            if monitor.should_abort():
                reason = monitor.get_abort_reason()
                stats = monitor.get_stats()
                print(f"\n🛑 {reason}")
                print(f"   Stats: {stats['passed']} pass / {stats['failed']} fail "
                      f"({stats['fail_rate']}%)")
                log_file.write(f"\n[WRAPPER] {reason}\n")
                log_file.write(f"[WRAPPER] Stats: {stats}\n")

                kill_process_tree(process.pid)
                result['return_code'] = -3
                result['early_abort'] = True
                result['abort_reason'] = reason
                result['killed'] = True
                break

        # Process finished normally
        if not result['killed']:
            reader_thread.join(timeout=30)
            result['return_code'] = process.returncode

    except Exception as e:
        print(f"❌ Wrapper error: {e}")
        log_file.write(f"\n[WRAPPER] Exception: {e}\n")
        result['return_code'] = -2
    finally:
        monitor.stop()
        result['duration_sec'] = time.time() - start_time
        log_file.write(f"\n[WRAPPER] Finished. Duration: {result['duration_sec']:.0f}s\n")
        log_file.write(f"[WRAPPER] Result: {result}\n")
        log_file.close()

    # Summary
    print(f"\n{'='*70}")
    if result.get('early_abort'):
        print(f"🛑 Execution ABORTED due to mass failures")
        print(f"   Reason: {result['abort_reason']}")
    elif result['hang_detected']:
        print(f"🔴 Execution KILLED due to hang/timeout")
        print(f"   Hung test: {result['hung_test']}")
    else:
        print(f"{'✅' if result['return_code'] == 0 else '⚠️'} Execution completed (exit code: {result['return_code']})")
    print(f"   Duration: {result['duration_sec']:.0f}s")
    print(f"   Log: {log_path}")
    print(f"{'='*70}\n")

    return result


def run_with_hang_recovery(
    test_suites: List[str],
    outputdir: str,
    robot_extra_args: List[str],
    hang_timeout: int,
    test_timeout: int,
    suite_timeout: int,
    tracker: 'TestTracker' = None,
    max_consecutive_fails: int = DEFAULT_MAX_CONSECUTIVE_FAILS,
    fail_rate_abort: int = DEFAULT_FAIL_RATE_ABORT,
) -> dict:
    """
    Run test suites with automatic hang recovery.

    Strategy:
    1. Try running all suites at once
    2. If hang detected → kill, collect partial results
    3. Re-run remaining suites (excluding the hung one)
    4. Merge all partial results
    """
    all_outputs = []
    hung_tests = []
    remaining_suites = list(test_suites)
    attempt = 0
    max_hang_recovery = 5  # Max times we'll try to recover from hangs

    while remaining_suites and attempt < max_hang_recovery:
        attempt += 1
        run_outputdir = os.path.join(outputdir, f'run_{attempt}')

        # Build robot args
        robot_args = list(robot_extra_args)
        robot_args.extend(['--outputdir', run_outputdir])

        # If we're recovering from a hang, exclude previously hung tests
        for hung in hung_tests:
            if hung:
                # Try to exclude by test name (Robot Framework --exclude by tag or --skip)
                robot_args.extend(['--skip', f'*{hung}*'])

        robot_args.extend(remaining_suites)

        print(f"\n🔄 Run attempt {attempt}/{max_hang_recovery}")
        if hung_tests:
            print(f"   Skipping previously hung tests: {hung_tests}")

        result = run_robot_with_hang_detection(
            robot_args=robot_args,
            outputdir=run_outputdir,
            hang_timeout=hang_timeout,
            test_timeout=test_timeout,
            suite_timeout=suite_timeout,
            run_label=f"attempt_{attempt}",
            tracker=tracker,
            max_consecutive_fails=max_consecutive_fails,
            fail_rate_abort=fail_rate_abort,
        )

        # Collect output.xml if it exists
        output_xml = os.path.join(run_outputdir, 'output.xml')
        if os.path.exists(output_xml):
            all_outputs.append(output_xml)

        if result.get('early_abort'):
            # Mass failure detected - stop completely
            print(f"🛑 Early abort triggered: {result.get('abort_reason', '')}")
            break

        if not result['hang_detected']:
            # No hang - we're done
            break

        # Hang detected - record and continue
        hung_tests.append(result['hung_test'])
        print(f"⚠️ Will skip hung test '{result['hung_test']}' and retry remaining...")

    # Merge all partial results
    final_result = {
        'total_attempts': attempt,
        'hung_tests': hung_tests,
        'output_files': all_outputs,
        'final_output': None,
    }

    if len(all_outputs) == 0:
        print("❌ No output files generated!")
        return final_result

    if len(all_outputs) == 1:
        final_result['final_output'] = all_outputs[0]
    else:
        # Merge with rebot
        merged_dir = os.path.join(outputdir, 'merged')
        os.makedirs(merged_dir, exist_ok=True)
        merge_cmd = ['rebot', '--merge', '--outputdir', merged_dir, '--nostatusrc']
        merge_cmd.extend(all_outputs)

        print(f"\n📦 Merging {len(all_outputs)} output files...")
        try:
            subprocess.run(merge_cmd, check=False, capture_output=True, text=True)
            merged_output = os.path.join(merged_dir, 'output.xml')
            if os.path.exists(merged_output):
                final_result['final_output'] = merged_output
        except Exception as e:
            print(f"⚠️ Merge failed: {e}. Using last output.")
            final_result['final_output'] = all_outputs[-1]

    # Generate hang report
    if hung_tests:
        hang_report_path = os.path.join(outputdir, 'hang_report.txt')
        with open(hang_report_path, 'w', encoding='utf-8') as f:
            f.write(f"HANG DETECTION REPORT\n")
            f.write(f"{'='*50}\n")
            f.write(f"Generated: {datetime.now().isoformat()}\n")
            f.write(f"Total attempts: {attempt}\n")
            f.write(f"Hung tests ({len(hung_tests)}):\n")
            for i, test in enumerate(hung_tests, 1):
                f.write(f"  {i}. {test}\n")
        print(f"📄 Hang report: {hang_report_path}")

    return final_result


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Robot Framework Wrapper with Hang Detection for HIL Testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default timeouts (5min hang, 10min per test)
  python test_runner_wrapper.py --suite tests/HVAC --outputdir results/HVAC

  # Custom timeouts
  python test_runner_wrapper.py --suite tests/ --outputdir results/ ^
      --hang-timeout 180 --test-timeout 300

  # Pass extra robot arguments after --
  python test_runner_wrapper.py --suite tests/ --outputdir results/ ^
      -- --variable VARIANT:A --include feature:HVAC --loglevel DEBUG

  # Multiple suites
  python test_runner_wrapper.py --suite tests/HVAC --suite tests/BCM ^
      --outputdir results/batch1
        """
    )

    parser.add_argument('--suite', action='append', required=True,
        help='Test suite path(s) to run. Can specify multiple times.')
    parser.add_argument('--outputdir', required=True,
        help='Output directory for results')
    parser.add_argument('--hang-timeout', type=int, default=DEFAULT_HANG_TIMEOUT,
        help=f'Seconds of no output before declaring hang (default: {DEFAULT_HANG_TIMEOUT})')
    parser.add_argument('--test-timeout', type=int, default=DEFAULT_TEST_TIMEOUT,
        help=f'Max seconds per individual test (default: {DEFAULT_TEST_TIMEOUT})')
    parser.add_argument('--suite-timeout', type=int, default=DEFAULT_SUITE_TIMEOUT,
        help=f'Max seconds for entire suite run (default: {DEFAULT_SUITE_TIMEOUT})')
    parser.add_argument('--no-recovery', action='store_true',
        help='Disable automatic hang recovery (just kill and stop)')
    # ─── Early Abort (Fail-Fast) ───
    parser.add_argument('--max-consecutive-fails', type=int,
        default=DEFAULT_MAX_CONSECUTIVE_FAILS,
        help=f'Abort if N tests fail consecutively (default: {DEFAULT_MAX_CONSECUTIVE_FAILS}). Set 0 to disable.')
    parser.add_argument('--fail-rate-abort', type=int,
        default=DEFAULT_FAIL_RATE_ABORT,
        help=f'Abort if fail rate exceeds N%% after {DEFAULT_MIN_TESTS_FOR_RATE} tests (default: {DEFAULT_FAIL_RATE_ABORT}%%). Set 0 to disable.')
    # ─── Realtime Tracker ───
    parser.add_argument('--tracker-file', default=None,
        help='Path to realtime tracker JSON file (updated after each test)')
    parser.add_argument('--bench-name', default='unknown',
        help='Bench identifier for tracking (e.g. hil-bench-1)')
    parser.add_argument('--feature-name', default='unknown',
        help='Feature name for tracking (e.g. eCall, HVAC)')
    # ─── Selection ───
    parser.add_argument('--selection-argfile', default=None,
        help='Robot Framework argfile for test selection (generated by tcid_mapper)')
    parser.add_argument('robot_args', nargs='*',
        help='Additional arguments passed to robot command (after --)')

    args = parser.parse_args()

    # ─── Initialize realtime tracker (optional) ───
    tracker = None
    if args.tracker_file and TestTracker:
        tracker = TestTracker(
            tracker_file=args.tracker_file,
            bench=args.bench_name,
            feature=args.feature_name,
        )
        print(f"📊 Realtime tracker: {args.tracker_file}")
    elif args.tracker_file and not TestTracker:
        print("⚠️ Tracker requested but realtime_tracker module not found. Skipping.")

    # ─── Selection argfile → inject into robot_args ───
    extra_robot_args = list(args.robot_args)
    if args.selection_argfile:
        extra_robot_args = ['--argumentfile', args.selection_argfile] + extra_robot_args
        print(f"📋 Using selection argfile: {args.selection_argfile}")

    print(f"""
╔════════════════════════════════════════════════════════════╗
║  🤖 Robot Framework Wrapper with Hang Detection           ║
║  Suites:       {', '.join(args.suite):<42s} ║
║  Hang timeout: {args.hang_timeout:<42d} ║
║  Test timeout: {args.test_timeout:<42d} ║
║  Suite timeout:{args.suite_timeout:<42d} ║
║  Tracker:      {('ON → ' + str(args.tracker_file)) if tracker else 'OFF':<42s} ║
╚════════════════════════════════════════════════════════════╝
    """)

    if args.no_recovery:
        result = run_robot_with_hang_detection(
            robot_args=extra_robot_args + args.suite,
            outputdir=args.outputdir,
            hang_timeout=args.hang_timeout,
            test_timeout=args.test_timeout,
            suite_timeout=args.suite_timeout,
            tracker=tracker,
            max_consecutive_fails=args.max_consecutive_fails,
            fail_rate_abort=args.fail_rate_abort,
        )
        sys.exit(0 if result['return_code'] >= 0 else 1)
    else:
        result = run_with_hang_recovery(
            test_suites=args.suite,
            outputdir=args.outputdir,
            robot_extra_args=extra_robot_args,
            hang_timeout=args.hang_timeout,
            test_timeout=args.test_timeout,
            suite_timeout=args.suite_timeout,
            tracker=tracker,
            max_consecutive_fails=args.max_consecutive_fails,
            fail_rate_abort=args.fail_rate_abort,
        )

        if result['hung_tests']:
            print(f"\n⚠️ {len(result['hung_tests'])} test(s) were killed due to hang:")
            for t in result['hung_tests']:
                print(f"   🔴 {t}")

        if result['final_output']:
            print(f"\n📊 Final output: {result['final_output']}")
            sys.exit(0)
        else:
            print("\n❌ No results generated!")
            sys.exit(1)


if __name__ == "__main__":
    main()
