#!/usr/bin/env python3
"""
HIL Test Automation - Failure Classifier & Retry Manager
=========================================================

Phân tích Robot Framework output.xml để:
1. Classify failures → ENV_FAIL / FLAKY / SCRIPT_FAIL
2. Generate report với failure breakdown
3. Support auto-retry workflow trong Jenkins pipeline

Usage (CLI):
    python failure_classifier.py count-env-fail --output-xml output.xml
    python failure_classifier.py list-retry     --output-xml output.xml
    python failure_classifier.py report         --output-xml output.xml --report-dir ./report
    python failure_classifier.py summary        --output-xml output.xml --output-html summary.html
    python failure_classifier.py weekly-report  --results-dir results/ --output-dir reports/weekly

Requirements:
    pip install robotframework
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree

try:
    from robot.api import ExecutionResult
except ImportError:
    print("ERROR: robotframework package not installed. Run: pip install robotframework")
    sys.exit(1)


# =============================================================================
# FAILURE PATTERNS - Customize these for your project!
# =============================================================================

# Patterns that indicate ENVIRONMENT issues (bench, connection, hardware)
ENV_FAILURE_PATTERNS: List[Tuple[str, str]] = [
    # ─── CAN / CANoe / Vector ───
    (r"(?i)CAN\s*(bus\s*)?(timeout|error|off|not\s*found)",              "CAN Bus Issue"),
    (r"(?i)CANoe?\s*(error|timeout|not\s*(found|running|connected))",    "CANoe Issue"),
    (r"(?i)vector\s*(hardware|channel|device)\s*(error|not\s*found)",    "Vector HW Issue"),
    (r"(?i)CAPL\s*(error|timeout|exception)",                            "CAPL Error"),
    (r"(?i)XL\s*Driver\s*(error|not\s*found)",                           "XL Driver Issue"),

    # ─── Connection / Communication ───
    (r"(?i)connection\s*(refused|reset|timeout|lost|closed|failed)",     "Connection Issue"),
    (r"(?i)socket\s*(error|timeout|closed|refused)",                     "Socket Error"),
    (r"(?i)ping\s*(failed|timeout|unreachable)",                         "Network Unreachable"),
    (r"(?i)ssh\s*(connection|timeout|refused|error)",                    "SSH Issue"),
    (r"(?i)TCP\s*(error|timeout|reset|refused)",                         "TCP Issue"),

    # ─── Hardware / Bench ───
    (r"(?i)no\s*response\s*from\s*(ECU|DUT|device|target)",             "No ECU Response"),
    (r"(?i)hardware\s*(not\s*found|disconnected|error|unavailable)",     "Hardware Issue"),
    (r"(?i)serial\s*port\s*(error|unavailable|busy|not\s*found)",        "Serial Port Issue"),
    (r"(?i)bench\s*(reset|reboot|not\s*ready|error|offline)",            "Bench Issue"),
    (r"(?i)power\s*supply\s*(error|off|unstable|timeout)",               "Power Supply Issue"),
    (r"(?i)relay\s*(board\s*)?(error|timeout|not\s*responding)",          "Relay Board Issue"),
    (r"(?i)DAQ\s*(error|timeout|not\s*(found|connected))",               "DAQ Issue"),

    # ─── Diagnostics (UDS/DoIP) ───
    (r"(?i)UDS\s*(timeout|no\s*response|error)",                         "UDS Timeout"),
    (r"(?i)DoIP\s*(connection|timeout|error)",                           "DoIP Issue"),
    (r"(?i)diagnostic\s*(session|timeout|error|failed\s*to\s*connect)",  "Diag Session Issue"),
    (r"(?i)negative\s*response.*?(0x10|0x11|0x12|0x13|0x14|0x21|0x22)", "NRC Response"),

    # ─── File / Resource ───
    (r"(?i)file\s*not\s*found.*?(\.dbc|\.arxml|\.cdd|\.odx|\.a2l)",     "Config File Missing"),
    (r"(?i)(license|dongle)\s*(error|expired|not\s*found)",              "License Issue"),

    # ─── General Timeout ───
    (r"(?i)(?<!assert)(?<!expect)timeout\s*(error|exceeded|waiting)",    "General Timeout"),
    (r"(?i)timed?\s*out\s*(waiting|connecting|reading)",                 "Timed Out"),

    # ─── Hang Detection (from test_runner_wrapper.py) ───
    (r"(?i)test\s*timeout\s*\d+\s*seconds?\s*exceeded",                 "Test Timeout (Hung)"),
    (r"(?i)HANG.*(DETECTED|TIMEOUT|KILLED)",                            "Hang Detected"),
    (r"(?i)process\s*(killed|terminated)\s*(due\s*to|by)\s*(hang|timeout)", "Process Killed (Hang)"),
    (r"(?i)execution\s*forcefully\s*(stopped|terminated|killed)",        "Forced Termination"),
    (r"(?i)no\s*output\s*for\s*\d+\s*seconds",                          "No Output Hang"),

    # ─── Robot Framework native timeout ───
    (r"(?i)Test\s*timeout\s+\d+\s*(second|minute)",                     "RF Test Timeout"),

    # ─── eCall Specific (TC phức tạp, nhiều step adhoc, multi-system) ───
    (r"(?i)modem\s*(timeout|error|not\s*(found|ready|responding))",      "eCall Modem Issue"),
    (r"(?i)audio\s*(path|channel|codec)\s*(error|timeout|not\s*ready)",  "eCall Audio Path Issue"),
    (r"(?i)IVS\s*(timeout|error|no\s*response|not\s*ready)",            "eCall IVS Issue"),
    (r"(?i)MSD\s*(send|transmit|encode)\s*(fail|error|timeout)",        "eCall MSD Failure"),
    (r"(?i)emergency\s*call\s*(setup|establish)\s*(fail|timeout)",       "eCall Setup Failure"),
    (r"(?i)PSAP\s*(connection|timeout|no\s*response|error)",            "eCall PSAP Issue"),
    (r"(?i)E112\s*(error|timeout|routing)",                             "eCall E112 Issue"),
    (r"(?i)(cellular|mobile|GSM|LTE)\s*(network|signal)\s*(error|timeout|lost|weak)",
                                                                        "eCall Network Instability"),
    (r"(?i)call\s*(drop|disconnect|terminate).*?unexpected",             "eCall Unexpected Drop"),
    (r"(?i)SOS\s*(button|trigger)\s*(error|not\s*detected|timeout)",     "eCall Trigger Issue"),
    (r"(?i)gnss|gps\s*(fix|signal|timeout|error|no\s*position)",        "eCall GNSS Issue"),
    (r"(?i)in.?band\s*modem\s*(error|timeout|sync)",                    "eCall In-Band Modem"),
]

# Patterns that indicate FLAKY behavior (intermittent pass/fail)
FLAKY_PATTERNS: List[Tuple[str, str]] = [
    (r"(?i)timing\s*(issue|error|mismatch|violation)",     "Timing Issue"),
    (r"(?i)intermittent",                                   "Intermittent"),
    (r"(?i)race\s*condition",                               "Race Condition"),
    (r"(?i)unexpected\s*state\s*transition",                "State Transition"),
    (r"(?i)signal\s*value\s*(fluctuat|unstable|bounce)",    "Signal Fluctuation"),
]

# Additional patterns you can add for your specific project
CUSTOM_PATTERNS: List[Tuple[str, str, str]] = [
    # (pattern, description, category)
    # Example: (r"(?i)my_custom_error", "Custom Error", "ENV_FAIL"),
]


# =============================================================================
# CORE CLASSIFIER
# =============================================================================

class FailureCategory:
    ENV_FAIL = "ENV_FAIL"
    FLAKY = "FLAKY"
    SCRIPT_FAIL = "SCRIPT_FAIL"


class FailureInfo:
    """Stores classification info for a single failed test."""

    def __init__(self, test_name: str, test_longname: str, message: str,
                 source: str, category: str, pattern_description: str,
                 tags: List[str] = None, elapsed: float = 0.0):
        self.test_name = test_name
        self.test_longname = test_longname
        self.message = message
        self.source = source or ""
        self.category = category
        self.pattern_description = pattern_description
        self.tags = tags or []
        self.elapsed = elapsed

    def to_dict(self) -> dict:
        return {
            "test_name": self.test_name,
            "test_longname": self.test_longname,
            "category": self.category,
            "pattern": self.pattern_description,
            "message": self.message[:500],  # Truncate long messages
            "source": self.source,
            "tags": self.tags,
            "elapsed_sec": round(self.elapsed, 2),
        }


def classify_failure(message: str) -> Tuple[str, str]:
    """
    Classify a test failure based on its error message.
    Returns: (category, pattern_description)
    """
    if not message:
        return FailureCategory.SCRIPT_FAIL, "Empty error message"

    # Check custom patterns first
    for pattern, description, category in CUSTOM_PATTERNS:
        if re.search(pattern, message):
            return category, description

    # Check environment failure patterns
    for pattern, description in ENV_FAILURE_PATTERNS:
        if re.search(pattern, message):
            return FailureCategory.ENV_FAIL, description

    # Check flaky patterns
    for pattern, description in FLAKY_PATTERNS:
        if re.search(pattern, message):
            return FailureCategory.FLAKY, description

    # Default: Script/DUT failure (actual test issue)
    return FailureCategory.SCRIPT_FAIL, "Test assertion or logic failure"


def analyze_output(output_xml: str) -> Dict[str, List[FailureInfo]]:
    """
    Parse Robot Framework output.xml and classify all failures.
    Returns dict grouped by category.
    """
    result = ExecutionResult(output_xml)
    classified = {
        FailureCategory.ENV_FAIL: [],
        FailureCategory.FLAKY: [],
        FailureCategory.SCRIPT_FAIL: [],
    }

    stats = {"total": 0, "passed": 0, "failed": 0}

    for test in result.suite.all_tests:
        stats["total"] += 1
        if test.status == "PASS":
            stats["passed"] += 1
            continue

        stats["failed"] += 1
        category, description = classify_failure(test.message or "")

        # Calculate elapsed time
        elapsed = 0.0
        if test.elapsedtime:
            elapsed = test.elapsedtime / 1000.0  # ms → sec

        info = FailureInfo(
            test_name=test.name,
            test_longname=str(test.longname) if test.longname else test.name,
            message=test.message or "",
            source=str(test.source) if test.source else "",
            category=category,
            pattern_description=description,
            tags=[str(t) for t in test.tags] if test.tags else [],
            elapsed=elapsed,
        )
        classified[category].append(info)

    return classified, stats


# =============================================================================
# CLI COMMANDS
# =============================================================================

def cmd_count_env_fail(args):
    """Count environment failures. Used by Jenkins to decide retry."""
    classified, stats = analyze_output(args.output_xml)
    count = len(classified[FailureCategory.ENV_FAIL]) + len(classified[FailureCategory.FLAKY])
    print(count)
    return count


def cmd_list_retry(args):
    """List test cases that should be retried (ENV_FAIL + FLAKY)."""
    classified, stats = analyze_output(args.output_xml)
    retry_list = []

    for category in [FailureCategory.ENV_FAIL, FailureCategory.FLAKY]:
        for info in classified[category]:
            retry_list.append(info.to_dict())

    if args.format == "json":
        print(json.dumps(retry_list, indent=2, ensure_ascii=False))
    else:
        # Simple text format
        for item in retry_list:
            print(f"[{item['category']}] {item['test_longname']} | {item['pattern']}")

    return retry_list


def cmd_report(args):
    """Generate classified failure report."""
    classified, stats = analyze_output(args.output_xml)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    # ── JSON Report ──
    report_data = {
        "generated_at": datetime.now().isoformat(),
        "source": args.output_xml,
        "statistics": {
            "total": stats["total"],
            "passed": stats["passed"],
            "failed": stats["failed"],
            "pass_rate_raw": round(stats["passed"] / max(stats["total"], 1) * 100, 1),
            "env_fail_count": len(classified[FailureCategory.ENV_FAIL]),
            "flaky_count": len(classified[FailureCategory.FLAKY]),
            "script_fail_count": len(classified[FailureCategory.SCRIPT_FAIL]),
        },
        "env_failures": [f.to_dict() for f in classified[FailureCategory.ENV_FAIL]],
        "flaky_failures": [f.to_dict() for f in classified[FailureCategory.FLAKY]],
        "script_failures": [f.to_dict() for f in classified[FailureCategory.SCRIPT_FAIL]],
    }

    json_path = report_dir / "classified_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)

    # ── Text Summary ──
    txt_path = report_dir / "failure_summary.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write("HIL TEST FAILURE CLASSIFICATION REPORT\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 70 + "\n\n")

        f.write(f"Total TCs:      {stats['total']}\n")
        f.write(f"Passed:         {stats['passed']}\n")
        f.write(f"Failed:         {stats['failed']}\n")
        f.write(f"Pass Rate:      {report_data['statistics']['pass_rate_raw']}%\n\n")

        f.write("-" * 70 + "\n")
        f.write("FAILURE BREAKDOWN:\n")
        f.write(f"  🟡 ENV_FAIL:    {len(classified[FailureCategory.ENV_FAIL]):4d}  (auto-retryable)\n")
        f.write(f"  🟠 FLAKY:       {len(classified[FailureCategory.FLAKY]):4d}  (auto-retryable)\n")
        f.write(f"  🔴 SCRIPT_FAIL: {len(classified[FailureCategory.SCRIPT_FAIL]):4d}  (manual analysis needed)\n")
        f.write("-" * 70 + "\n\n")

        # Group env failures by pattern
        if classified[FailureCategory.ENV_FAIL]:
            f.write("ENV_FAIL DETAILS (grouped by root cause):\n")
            pattern_groups = {}
            for info in classified[FailureCategory.ENV_FAIL]:
                pattern_groups.setdefault(info.pattern_description, []).append(info)

            for pattern, infos in sorted(pattern_groups.items(), key=lambda x: -len(x[1])):
                f.write(f"\n  [{pattern}] - {len(infos)} failure(s):\n")
                for info in infos[:5]:  # Show max 5 per pattern
                    f.write(f"    • {info.test_name}\n")
                if len(infos) > 5:
                    f.write(f"    ... and {len(infos) - 5} more\n")

        if classified[FailureCategory.SCRIPT_FAIL]:
            f.write("\n\nSCRIPT_FAIL DETAILS (needs manual analysis):\n")
            for info in classified[FailureCategory.SCRIPT_FAIL]:
                f.write(f"  • {info.test_name}\n")
                f.write(f"    Message: {info.message[:200]}\n")
                f.write(f"    Source:  {info.source}\n\n")

    # ── CSV for Excel export ──
    csv_path = report_dir / "all_failures.csv"
    with open(csv_path, "w", encoding="utf-8-sig") as f:  # utf-8-sig for Excel
        f.write("Test Name,Long Name,Category,Pattern,Message,Source,Tags,Elapsed (sec)\n")
        for category in [FailureCategory.ENV_FAIL, FailureCategory.FLAKY, FailureCategory.SCRIPT_FAIL]:
            for info in classified[category]:
                msg = info.message.replace('"', '""')[:300]
                tags = "|".join(info.tags)
                f.write(f'"{info.test_name}","{info.test_longname}","{info.category}",')
                f.write(f'"{info.pattern_description}","{msg}","{info.source}",')
                f.write(f'"{tags}",{info.elapsed:.2f}\n')

    print(f"✅ Report generated in: {report_dir}")
    print(f"  - {json_path.name} (JSON)")
    print(f"  - {txt_path.name} (Text)")
    print(f"  - {csv_path.name} (CSV/Excel)")


def cmd_summary(args):
    """Generate HTML summary for Jenkins email notification."""
    classified, stats = analyze_output(args.output_xml)

    pass_rate = round(stats["passed"] / max(stats["total"], 1) * 100, 1)
    env_count = len(classified[FailureCategory.ENV_FAIL])
    flaky_count = len(classified[FailureCategory.FLAKY])
    script_count = len(classified[FailureCategory.SCRIPT_FAIL])
    retryable = env_count + flaky_count

    # Pass rate color
    if pass_rate >= 85:
        rate_color = "#27ae60"
        rate_emoji = "✅"
    elif pass_rate >= 60:
        rate_color = "#f39c12"
        rate_emoji = "⚠️"
    else:
        rate_color = "#e74c3c"
        rate_emoji = "❌"

    html = f"""<!DOCTYPE html>
<html>
<head>
<style>
    body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #333; max-width: 800px; margin: 0 auto; }}
    .header {{ background: linear-gradient(135deg, #1a1a2e, #16213e); color: white; padding: 20px; border-radius: 8px; }}
    .header h1 {{ margin: 0; font-size: 20px; }}
    .stats-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0; }}
    .stat-card {{ background: #f8f9fa; border-radius: 8px; padding: 16px; text-align: center; border-left: 4px solid; }}
    .stat-card .value {{ font-size: 28px; font-weight: bold; }}
    .stat-card .label {{ font-size: 12px; color: #666; text-transform: uppercase; }}
    .pass {{ border-color: #27ae60; }} .pass .value {{ color: #27ae60; }}
    .fail {{ border-color: #e74c3c; }} .fail .value {{ color: #e74c3c; }}
    .env {{ border-color: #f39c12; }} .env .value {{ color: #f39c12; }}
    .rate {{ border-color: {rate_color}; }} .rate .value {{ color: {rate_color}; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
    th {{ background: #1a1a2e; color: white; padding: 10px; text-align: left; }}
    td {{ padding: 8px 10px; border-bottom: 1px solid #eee; }}
    tr:hover {{ background: #f5f5f5; }}
    .tag {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
    .tag-env {{ background: #fff3cd; color: #856404; }}
    .tag-flaky {{ background: #ffeaa7; color: #d35400; }}
    .tag-script {{ background: #f8d7da; color: #721c24; }}
    .footer {{ margin-top: 20px; padding: 12px; background: #f8f9fa; border-radius: 8px; font-size: 12px; color: #666; }}
</style>
</head>
<body>
    <div class="header">
        <h1>{rate_emoji} HIL Test Execution Report</h1>
        <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>

    <div class="stats-grid">
        <div class="stat-card rate">
            <div class="value">{pass_rate}%</div>
            <div class="label">Pass Rate</div>
        </div>
        <div class="stat-card pass">
            <div class="value">{stats['passed']}</div>
            <div class="label">Passed</div>
        </div>
        <div class="stat-card fail">
            <div class="value">{stats['failed']}</div>
            <div class="label">Failed</div>
        </div>
        <div class="stat-card env">
            <div class="value">{retryable}</div>
            <div class="label">Env/Flaky (Retryable)</div>
        </div>
    </div>

    <h3>Failure Breakdown</h3>
    <table>
        <tr>
            <th>Category</th>
            <th>Count</th>
            <th>Action</th>
        </tr>
        <tr>
            <td><span class="tag tag-env">🟡 ENV_FAIL</span></td>
            <td>{env_count}</td>
            <td>Auto-retried by pipeline</td>
        </tr>
        <tr>
            <td><span class="tag tag-flaky">🟠 FLAKY</span></td>
            <td>{flaky_count}</td>
            <td>Auto-retried + flagged for review</td>
        </tr>
        <tr>
            <td><span class="tag tag-script">🔴 SCRIPT_FAIL</span></td>
            <td>{script_count}</td>
            <td>Manual analysis required</td>
        </tr>
    </table>
"""

    # Add top script failures for quick review
    if classified[FailureCategory.SCRIPT_FAIL]:
        html += """
    <h3>🔴 Script Failures (Top 20 - Need Manual Analysis)</h3>
    <table>
        <tr><th>#</th><th>Test Case</th><th>Error Message</th></tr>
"""
        for i, info in enumerate(classified[FailureCategory.SCRIPT_FAIL][:20], 1):
            msg = info.message[:150].replace("<", "&lt;").replace(">", "&gt;")
            html += f"        <tr><td>{i}</td><td>{info.test_name}</td><td>{msg}</td></tr>\n"

        remaining = len(classified[FailureCategory.SCRIPT_FAIL]) - 20
        if remaining > 0:
            html += f"        <tr><td colspan='3'><i>... and {remaining} more</i></td></tr>\n"
        html += "    </table>\n"

    # Add env failure summary grouped by pattern
    if classified[FailureCategory.ENV_FAIL]:
        html += """
    <h3>🟡 Environment Failures (Grouped by Root Cause)</h3>
    <table>
        <tr><th>Root Cause</th><th>Count</th><th>Example Test</th></tr>
"""
        pattern_groups = {}
        for info in classified[FailureCategory.ENV_FAIL]:
            pattern_groups.setdefault(info.pattern_description, []).append(info)

        for pattern, infos in sorted(pattern_groups.items(), key=lambda x: -len(x[1])):
            example = infos[0].test_name
            html += f"        <tr><td>{pattern}</td><td>{len(infos)}</td><td>{example}</td></tr>\n"
        html += "    </table>\n"

    html += f"""
    <div class="footer">
        <p>📧 This report was auto-generated by the HIL CI/CD Pipeline.</p>
        <p>📁 Full details available in Jenkins build artifacts.</p>
    </div>
</body>
</html>"""

    output_path = Path(args.output_html)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ HTML summary generated: {output_path}")


def cmd_weekly_report(args):
    """
    Generate weekly partial report aggregating multiple nightly runs.
    Scans results directory for output.xml files, aggregates stats,
    tracks coverage and trend over time.
    """
    results_dir = Path(args.results_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find all output.xml files in results directory
    output_xmls = sorted(results_dir.rglob('output.xml'))
    if not output_xmls:
        print(f"⚠️ No output.xml files found in {results_dir}")
        sys.exit(1)

    print(f"📊 Found {len(output_xmls)} output.xml files")

    # Aggregate results from all runs
    all_tests = {}     # test_longname → {last_status, last_category, run_count, pass_count}
    daily_stats = []   # per-day aggregation
    total_env_fail = 0
    total_flaky = 0
    total_script_fail = 0
    total_hang = 0
    feature_stats = {} # feature → {total, passed, failed, env_fail}

    for xml_path in output_xmls:
        try:
            classified, stats = analyze_output(str(xml_path))
            run_date = datetime.fromtimestamp(xml_path.stat().st_mtime).strftime('%Y-%m-%d')

            daily_stats.append({
                'date': run_date,
                'path': str(xml_path),
                'total': stats['total'],
                'passed': stats['passed'],
                'failed': stats['failed'],
                'env_fail': len(classified[FailureCategory.ENV_FAIL]),
                'flaky': len(classified[FailureCategory.FLAKY]),
                'script_fail': len(classified[FailureCategory.SCRIPT_FAIL]),
            })

            total_env_fail += len(classified[FailureCategory.ENV_FAIL])
            total_flaky += len(classified[FailureCategory.FLAKY])
            total_script_fail += len(classified[FailureCategory.SCRIPT_FAIL])

            # Track per-test results (for coverage)
            result = ExecutionResult(str(xml_path))
            for test in result.suite.all_tests:
                name = str(test.longname) if test.longname else test.name
                if name not in all_tests:
                    all_tests[name] = {
                        'run_count': 0, 'pass_count': 0, 'fail_count': 0,
                        'last_status': None, 'tags': [],
                    }
                all_tests[name]['run_count'] += 1
                if test.status == 'PASS':
                    all_tests[name]['pass_count'] += 1
                else:
                    all_tests[name]['fail_count'] += 1
                all_tests[name]['last_status'] = test.status
                all_tests[name]['tags'] = [str(t) for t in test.tags] if test.tags else []

                # Track feature-level stats
                feature = 'Unknown'
                if test.tags:
                    for tag in test.tags:
                        tag_str = str(tag)
                        if tag_str.startswith('feature:'):
                            feature = tag_str.split(':')[1]
                            break
                if feature not in feature_stats:
                    feature_stats[feature] = {'total': 0, 'passed': 0, 'failed': 0, 'env_fail': 0}
                feature_stats[feature]['total'] += 1
                if test.status == 'PASS':
                    feature_stats[feature]['passed'] += 1
                else:
                    feature_stats[feature]['failed'] += 1

            # Count hang-related failures
            for info in classified[FailureCategory.ENV_FAIL]:
                if 'Hang' in info.pattern_description or 'Timeout (Hung)' in info.pattern_description:
                    total_hang += 1
                # Track env_fail per feature
                for tag in info.tags:
                    if tag.startswith('feature:'):
                        feat = tag.split(':')[1]
                        if feat in feature_stats:
                            feature_stats[feat]['env_fail'] += 1

        except Exception as e:
            print(f"⚠️ Error processing {xml_path}: {e}")

    # Calculate aggregated metrics
    unique_tests_executed = len(all_tests)
    total_target = int(args.total_target) if args.total_target else unique_tests_executed
    coverage_pct = round(unique_tests_executed / max(total_target, 1) * 100, 1)

    latest_pass_count = sum(1 for t in all_tests.values() if t['last_status'] == 'PASS')
    latest_pass_rate = round(latest_pass_count / max(unique_tests_executed, 1) * 100, 1)

    # Generate text report
    report_path = output_dir / 'weekly_report.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("╔══════════════════════════════════════════════════╗\n")
        f.write(f"║  WEEKLY PARTIAL REPORT                           ║\n")
        f.write(f"║  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}                    ║\n")
        f.write(f"║  Results from: {results_dir}\n")
        f.write("╠══════════════════════════════════════════════════╣\n")
        f.write(f"║  Coverage:     {unique_tests_executed} / {total_target} ({coverage_pct}%)\n")
        f.write(f"║  Pass Rate:    {latest_pass_rate}% (latest run status)\n")
        f.write(f"║  Nightly Runs: {len(daily_stats)}\n")
        f.write("╠═══ FAILURE BREAKDOWN ════════════════════════════╣\n")
        f.write(f"║  ENV_FAIL:     {total_env_fail:4d} (auto-retried)\n")
        f.write(f"║  HANG:         {total_hang:4d} (auto-recovered)\n")
        f.write(f"║  SCRIPT_FAIL:  {total_script_fail:4d} (manual analysis needed)\n")
        f.write(f"║  FLAKY:        {total_flaky:4d} (monitoring)\n")
        f.write("╠═══ TOP FAILING FEATURES ═════════════════════════╣\n")

        # Sort features by fail rate (worst first)
        sorted_features = sorted(
            feature_stats.items(),
            key=lambda x: x[1]['failed'] / max(x[1]['total'], 1),
            reverse=True
        )
        for feat, s in sorted_features[:10]:
            feat_rate = round(s['passed'] / max(s['total'], 1) * 100)
            risk = '⚠ HIGH RISK' if feat_rate < 50 else ''
            f.write(f"║  {feat:<12s} {s['passed']:>4d}/{s['total']:<4d} ({feat_rate:>2d}% pass) {risk}\n")

        f.write("╠═══ DAILY TREND ═══════════════════════════════════╣\n")
        for day in daily_stats[-7:]:
            day_rate = round(day['passed'] / max(day['total'], 1) * 100)
            bar = '█' * (day_rate // 5) + '░' * (20 - day_rate // 5)
            f.write(f"║  {day['date']}  {bar} {day_rate}% ({day['passed']}/{day['total']})\n")

        f.write("╚══════════════════════════════════════════════════╝\n")

    # Generate JSON for programmatic access
    json_path = output_dir / 'weekly_report.json'
    report_data = {
        'generated_at': datetime.now().isoformat(),
        'coverage': {'executed': unique_tests_executed, 'target': total_target, 'pct': coverage_pct},
        'latest_pass_rate': latest_pass_rate,
        'total_env_fail': total_env_fail,
        'total_hang': total_hang,
        'total_script_fail': total_script_fail,
        'total_flaky': total_flaky,
        'feature_stats': feature_stats,
        'daily_stats': daily_stats,
        'nightly_runs': len(daily_stats),
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Weekly report generated:")
    print(f"  📄 {report_path}")
    print(f"  📄 {json_path}")
    print(f"\n📊 Summary: {unique_tests_executed}/{total_target} TCs ({coverage_pct}% coverage), {latest_pass_rate}% pass rate")
    print(f"   ENV_FAIL={total_env_fail} | HANG={total_hang} | SCRIPT_FAIL={total_script_fail} | FLAKY={total_flaky}")


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="HIL Test Failure Classifier - Classify Robot Framework failures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Count environment failures (used by Jenkins retry logic)
  python failure_classifier.py count-env-fail --output-xml output.xml

  # List all retryable failures as JSON
  python failure_classifier.py list-retry --output-xml output.xml --format json

  # Generate full classified report (JSON + TXT + CSV)
  python failure_classifier.py report --output-xml output.xml --report-dir ./reports

  # Generate HTML summary for email notification
  python failure_classifier.py summary --output-xml output.xml --output-html summary.html

  # Generate weekly partial report (aggregates multiple nightly runs)
  python failure_classifier.py weekly-report --results-dir results/Variant_A/ --output-dir reports/weekly
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── count-env-fail ──
    p_count = subparsers.add_parser("count-env-fail",
        help="Count retryable failures (ENV_FAIL + FLAKY)")
    p_count.add_argument("--output-xml", required=True, help="Path to output.xml")

    # ── list-retry ──
    p_list = subparsers.add_parser("list-retry",
        help="List test cases that should be retried")
    p_list.add_argument("--output-xml", required=True, help="Path to output.xml")
    p_list.add_argument("--format", choices=["json", "text"], default="text",
        help="Output format (default: text)")

    # ── report ──
    p_report = subparsers.add_parser("report",
        help="Generate classified failure report")
    p_report.add_argument("--output-xml", required=True, help="Path to output.xml")
    p_report.add_argument("--report-dir", required=True, help="Directory to write report files")

    # ── summary ──
    p_summary = subparsers.add_parser("summary",
        help="Generate HTML summary for email")
    p_summary.add_argument("--output-xml", required=True, help="Path to output.xml")
    p_summary.add_argument("--output-html", required=True, help="Path to write HTML summary")

    # ── weekly-report ──
    p_weekly = subparsers.add_parser("weekly-report",
        help="Generate weekly partial report (aggregates multiple nightly runs)")
    p_weekly.add_argument("--results-dir", required=True,
        help="Directory containing nightly results (scans recursively for output.xml)")
    p_weekly.add_argument("--output-dir", required=True,
        help="Directory to write weekly report")
    p_weekly.add_argument("--total-target", default=None,
        help="Total target TCs for coverage calculation (default: auto-detect)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Validate input file (not needed for weekly-report)
    if args.command != 'weekly-report':
        if not os.path.exists(args.output_xml):
            print(f"ERROR: File not found: {args.output_xml}")
            sys.exit(1)

    # Dispatch command
    commands = {
        "count-env-fail": cmd_count_env_fail,
        "list-retry": cmd_list_retry,
        "report": cmd_report,
        "summary": cmd_summary,
        "weekly-report": cmd_weekly_report,
    }

    try:
        commands[args.command](args)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
