#!/usr/bin/env python3
"""
HIL Test Automation - TCID Mapper
==================================

Map assigned TCIDs to Robot Framework test scripts.

Naming convention:
    TCID_TCName.robot
    Ví dụ: TC001_BasicEcall.robot, TC045_HVACTempControl.robot

Folder structure (feature = parent folder):
    tests/
    ├── eCall/
    │   ├── TC001_BasicEcall.robot        → feature = eCall
    │   ├── TC002_EcallWithAudio.robot
    │   └── TC003_EcallMSD.robot
    ├── HVAC/
    │   ├── TC045_HVACTempControl.robot   → feature = HVAC
    │   └── TC046_HVACFanSpeed.robot
    ├── BCM/
    │   └── TC100_DoorLock.robot          → feature = BCM
    └── ...

Usage:
    # Resolve TCIDs → list of .robot files
    python tcid_mapper.py resolve --tcid-list TC001,TC002,TC045 --test-dir tests/

    # Resolve from file (1 TCID per line)
    python tcid_mapper.py resolve --tcid-file tcids.txt --test-dir tests/

    # Show feature grouping for TCIDs
    python tcid_mapper.py group --tcid-list TC001,TC002,TC045 --test-dir tests/

    # Scan all tests and show inventory
    python tcid_mapper.py inventory --test-dir tests/

    # Generate Robot Framework arguments file
    python tcid_mapper.py generate-argfile --tcid-list TC001,TC045 --test-dir tests/ --output argfile.txt
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# =============================================================================
# CORE MAPPER
# =============================================================================

class TCIDMapper:
    """
    Map TCIDs to .robot test script files.
    
    Convention: filename = TCID_TCName.robot
    Feature = parent folder name
    """

    def __init__(self, test_dir: str):
        self.test_dir = Path(test_dir)
        if not self.test_dir.exists():
            raise FileNotFoundError(f"Test directory not found: {test_dir}")
        
        # Build index on init
        self._index: Dict[str, dict] = {}  # tcid → {path, feature, filename}
        self._build_index()

    def _extract_tcid(self, filename: str) -> Optional[str]:
        """
        Extract TCID from filename.
        Convention: TCID_TCName.robot
        
        Examples:
            TC001_BasicEcall.robot      → TC001
            TC_045_HVACTemp.robot       → TC_045
            REQ-ECALL-001_Test.robot    → REQ-ECALL-001
            12345_SomeTest.robot        → 12345
        
        Strategy: everything before the FIRST underscore that is followed
        by an alphabetic character is the TCID.
        Fallback: split on first '_' → TCID is the left part.
        """
        stem = Path(filename).stem  # Remove .robot extension
        
        # Pattern: TCID_Name where TCID can contain letters, digits, dashes
        # Stop at the first underscore followed by a letter (start of TC name)
        match = re.match(r'^([A-Za-z0-9_-]*?\d+(?:[-_]\d+)*)_([A-Za-z].*)', stem)
        if match:
            return match.group(1)
        
        # Fallback: just take everything before first underscore
        parts = stem.split('_', 1)
        if len(parts) >= 1:
            return parts[0]
        
        return None

    def _get_feature(self, robot_path: Path) -> str:
        """
        Get feature name from folder structure.
        Feature = immediate parent folder relative to test_dir.
        
        tests/eCall/TC001_Basic.robot     → eCall
        tests/HVAC/sub/TC045_Temp.robot   → HVAC
        tests/TC999_Standalone.robot      → _root_ (no feature folder)
        """
        try:
            rel_path = robot_path.relative_to(self.test_dir)
            parts = rel_path.parts
            if len(parts) > 1:
                return parts[0]  # First folder after test_dir
            return "_root_"
        except ValueError:
            return "_unknown_"

    def _build_index(self):
        """Scan test directory and build TCID index."""
        robot_files = list(self.test_dir.rglob('*.robot'))
        
        for robot_path in robot_files:
            tcid = self._extract_tcid(robot_path.name)
            if tcid:
                feature = self._get_feature(robot_path)
                self._index[tcid.upper()] = {
                    'tcid': tcid,
                    'path': str(robot_path),
                    'relative_path': str(robot_path.relative_to(self.test_dir)),
                    'filename': robot_path.name,
                    'feature': feature,
                    'suite_path': str(robot_path.parent.relative_to(self.test_dir)),
                }

        # Also store lowercase and original case for flexible matching
        extra = {}
        for tcid, info in self._index.items():
            extra[tcid.lower()] = info
            extra[info['tcid']] = info  # original case
        self._index.update(extra)

    def resolve(self, tcid: str) -> Optional[dict]:
        """Resolve a single TCID to its .robot file info."""
        # Try exact match, then case-insensitive
        return (self._index.get(tcid) 
                or self._index.get(tcid.upper()) 
                or self._index.get(tcid.lower()))

    def resolve_many(self, tcids: List[str]) -> Tuple[List[dict], List[str]]:
        """
        Resolve multiple TCIDs.
        Returns: (found_list, not_found_list)
        """
        found = []
        not_found = []
        seen = set()
        
        for tcid in tcids:
            tcid = tcid.strip()
            if not tcid:
                continue
            result = self.resolve(tcid)
            if result and result['path'] not in seen:
                found.append(result)
                seen.add(result['path'])
            elif result is None:
                not_found.append(tcid)
        
        return found, not_found

    def group_by_feature(self, tcids: List[str]) -> Dict[str, List[dict]]:
        """
        Resolve TCIDs and group by feature (folder).
        Returns: {feature_name: [test_info, ...]}
        """
        found, not_found = self.resolve_many(tcids)
        groups = defaultdict(list)
        for info in found:
            groups[info['feature']].append(info)
        
        if not_found:
            groups['_NOT_FOUND_'] = [{'tcid': t, 'path': None} for t in not_found]
        
        return dict(groups)

    def get_inventory(self) -> Dict[str, List[dict]]:
        """Get full test inventory grouped by feature."""
        inventory = defaultdict(list)
        seen = set()
        for tcid, info in self._index.items():
            if info['path'] not in seen:
                inventory[info['feature']].append(info)
                seen.add(info['path'])
        return dict(inventory)

    def generate_robot_args(self, tcids: List[str]) -> Tuple[str, List[str]]:
        """
        Generate Robot Framework --suite arguments for selected TCIDs.
        Groups by suite (folder) for efficiency.
        
        Returns: (args_string, not_found_tcids)
        """
        found, not_found = self.resolve_many(tcids)
        
        if not found:
            return "", not_found

        # Group by suite path for efficient execution
        suite_tests = defaultdict(list)
        for info in found:
            suite_tests[info['suite_path']].append(info)

        # Build --test arguments (Robot Framework uses test name matching)
        args_parts = []
        for suite_path, tests in suite_tests.items():
            args_parts.append(f"--suite {suite_path}")
            for test_info in tests:
                # Use filename stem as test selector
                test_name = Path(test_info['filename']).stem
                args_parts.append(f"--test *{test_info['tcid']}*")

        return " ".join(args_parts), not_found


# =============================================================================
# CLI COMMANDS
# =============================================================================

def _parse_tcid_input(args) -> List[str]:
    """Parse TCID input from either --tcid-list or --tcid-file."""
    tcids = []
    if args.tcid_list:
        # Comma or newline separated
        for item in args.tcid_list.replace('\n', ',').split(','):
            item = item.strip()
            if item:
                tcids.append(item)
    if hasattr(args, 'tcid_file') and args.tcid_file:
        with open(args.tcid_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    tcids.append(line)
    return tcids


def cmd_resolve(args):
    """Resolve TCIDs to .robot file paths."""
    mapper = TCIDMapper(args.test_dir)
    tcids = _parse_tcid_input(args)
    
    if not tcids:
        print("ERROR: No TCIDs provided. Use --tcid-list or --tcid-file")
        sys.exit(1)
    
    found, not_found = mapper.resolve_many(tcids)
    
    if args.format == 'json':
        output = {
            'found': found,
            'not_found': not_found,
            'stats': {
                'requested': len(tcids),
                'found': len(found),
                'not_found': len(not_found),
            }
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(f"{'─' * 70}")
        print(f"TCID Resolution: {len(found)}/{len(tcids)} found")
        print(f"{'─' * 70}")
        for info in found:
            print(f"  ✅ {info['tcid']:<20s} → {info['relative_path']:<40s} [{info['feature']}]")
        if not_found:
            print(f"\n  ⚠️  NOT FOUND ({len(not_found)}):")
            for tcid in not_found:
                print(f"  ❌ {tcid}")
        print(f"{'─' * 70}")
    
    # Exit code: 0 if all found, 1 if some missing
    return 0 if not not_found else 1


def cmd_group(args):
    """Show feature grouping for TCIDs."""
    mapper = TCIDMapper(args.test_dir)
    tcids = _parse_tcid_input(args)
    
    if not tcids:
        print("ERROR: No TCIDs provided")
        sys.exit(1)
    
    groups = mapper.group_by_feature(tcids)
    
    if args.format == 'json':
        print(json.dumps(groups, indent=2, ensure_ascii=False))
    else:
        print(f"\n📋 Feature Grouping ({len(tcids)} TCIDs)")
        print(f"{'═' * 60}")
        for feature, tests in sorted(groups.items()):
            if feature == '_NOT_FOUND_':
                continue
            print(f"\n  📁 {feature} ({len(tests)} TCs)")
            for info in tests:
                print(f"     • {info['tcid']} → {info['filename']}")
        
        if '_NOT_FOUND_' in groups:
            print(f"\n  ⚠️  NOT FOUND ({len(groups['_NOT_FOUND_'])} TCs)")
            for info in groups['_NOT_FOUND_']:
                print(f"     ❌ {info['tcid']}")
        print(f"{'═' * 60}")


def cmd_inventory(args):
    """Show full test inventory."""
    mapper = TCIDMapper(args.test_dir)
    inventory = mapper.get_inventory()
    
    total = sum(len(tests) for tests in inventory.values())
    
    if args.format == 'json':
        output = {
            'total': total,
            'features': {f: len(t) for f, t in inventory.items()},
            'tests': inventory,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(f"\n📦 Test Inventory: {total} test scripts")
        print(f"{'═' * 60}")
        for feature in sorted(inventory.keys()):
            tests = inventory[feature]
            print(f"\n  📁 {feature}/ ({len(tests)} TCs)")
            for info in sorted(tests, key=lambda x: x['tcid']):
                print(f"     {info['tcid']:<20s} {info['filename']}")
        print(f"\n{'═' * 60}")
        print(f"Total: {total} test scripts across {len(inventory)} features")


def cmd_generate_argfile(args):
    """Generate Robot Framework arguments file for selected TCIDs."""
    mapper = TCIDMapper(args.test_dir)
    tcids = _parse_tcid_input(args)
    
    if not tcids:
        print("ERROR: No TCIDs provided")
        sys.exit(1)
    
    found, not_found = mapper.resolve_many(tcids)
    
    if not found:
        print("ERROR: No TCIDs could be resolved")
        sys.exit(1)
    
    # Group by feature/suite for organized execution
    groups = mapper.group_by_feature(tcids)
    
    # Generate argfile content
    lines = [
        f"# Auto-generated by tcid_mapper.py",
        f"# TCIDs requested: {len(tcids)}",
        f"# TCIDs resolved:  {len(found)}",
        f"#",
    ]
    
    for feature, tests in sorted(groups.items()):
        if feature.startswith('_'):
            continue
        lines.append(f"# ─── {feature} ({len(tests)} TCs) ───")
        lines.append(f"--suite {feature}")
        for info in tests:
            # Use --test with TCID pattern to match specific tests
            lines.append(f"--test *{info['tcid']}*")
        lines.append("")
    
    argfile_content = "\n".join(lines)
    
    # Write to file or stdout
    output_path = args.output
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(argfile_content)
        print(f"✅ Argfile generated: {output_path}")
        print(f"   {len(found)} TCs across {len([f for f in groups if not f.startswith('_')])} features")
        if not_found:
            print(f"   ⚠️  {len(not_found)} TCIDs not found: {not_found}")
        print(f"\n   Usage: robot --argumentfile {output_path} tests/")
    else:
        print(argfile_content)
    
    # Also output not-found TCIDs to stderr for pipeline visibility
    if not_found:
        print(f"\nWARNING: {len(not_found)} TCIDs not found:", file=sys.stderr)
        for tcid in not_found:
            print(f"  - {tcid}", file=sys.stderr)


def cmd_resolve_folders(args):
    """
    Resolve folder paths to feature groups + argfile.
    Validates folders exist under test_dir, collects .robot files,
    and generates an argfile for Robot Framework.

    Usage:
        python tcid_mapper.py resolve-folders --folder-list eCall,HVAC --test-dir tests/
        python tcid_mapper.py resolve-folders --folder-list eCall/sub --test-dir tests/ --output argfile.txt
    """
    test_dir = Path(args.test_dir)
    if not test_dir.exists():
        print(f"ERROR: Test directory not found: {args.test_dir}")
        sys.exit(1)

    # Parse folder list
    folders = []
    if args.folder_list:
        for item in args.folder_list.replace('\n', ',').split(','):
            item = item.strip().strip('/').strip('\\')
            if item:
                folders.append(item)
    if hasattr(args, 'folder_file') and args.folder_file:
        with open(args.folder_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    folders.append(line)

    if not folders:
        print("ERROR: No folders provided. Use --folder-list or --folder-file")
        sys.exit(1)

    # Validate and scan each folder
    found_folders = []
    not_found_folders = []
    feature_groups = {}  # feature_name → [robot_file_paths]
    total_robot_files = 0

    for folder in folders:
        folder_path = test_dir / folder
        if not folder_path.exists() or not folder_path.is_dir():
            not_found_folders.append(folder)
            continue

        # Scan for .robot files
        robot_files = list(folder_path.rglob('*.robot'))
        if not robot_files:
            print(f"  ⚠️  {folder}: no .robot files found")
            not_found_folders.append(folder)
            continue

        found_folders.append(folder)
        # Feature name = top-level folder name (first component)
        feature_name = folder.split('/')[0].split('\\')[0]
        if feature_name not in feature_groups:
            feature_groups[feature_name] = []

        for rf in robot_files:
            rel_path = str(rf.relative_to(test_dir))
            feature_groups[feature_name].append({
                'path': str(rf),
                'relative_path': rel_path,
                'filename': rf.name,
                'feature': feature_name,
                'suite_path': str(rf.parent.relative_to(test_dir)),
            })
            total_robot_files += 1

    # Output
    if args.format == 'json':
        output = {
            'found_folders': found_folders,
            'not_found_folders': not_found_folders,
            'features': {f: files for f, files in feature_groups.items()},
            'stats': {
                'folders_requested': len(folders),
                'folders_found': len(found_folders),
                'folders_not_found': len(not_found_folders),
                'robot_files': total_robot_files,
            }
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(f"{'─' * 70}")
        print(f"Folder Resolution: {len(found_folders)}/{len(folders)} folders found, "
              f"{total_robot_files} .robot files")
        print(f"{'─' * 70}")
        for feature, files in sorted(feature_groups.items()):
            print(f"\n  📁 {feature} ({len(files)} .robot files)")
            for f in files[:10]:
                print(f"     • {f['relative_path']}")
            if len(files) > 10:
                print(f"     ... and {len(files) - 10} more")
        if not_found_folders:
            print(f"\n  ⚠️  NOT FOUND ({len(not_found_folders)}):")
            for nf in not_found_folders:
                print(f"  ❌ {nf}")
        print(f"{'─' * 70}")

    # Generate argfile if --output specified
    if hasattr(args, 'output') and args.output:
        lines = [
            f"# Auto-generated by tcid_mapper.py resolve-folders",
            f"# Folders: {', '.join(found_folders)}",
            f"# Robot files: {total_robot_files}",
            f"#",
        ]
        for folder in found_folders:
            lines.append(f"--suite {folder}")
        lines.append("")

        with open(args.output, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))
        print(f"✅ Argfile generated: {args.output}")

    if not_found_folders:
        print(f"\nWARNING: {len(not_found_folders)} folders not found:",
              file=sys.stderr)
        for nf in not_found_folders:
            print(f"  - {nf}", file=sys.stderr)

    return 0 if not not_found_folders else 1


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="HIL TCID Mapper - Map TCIDs to Robot Framework test scripts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Resolve TCIDs to .robot files
  python tcid_mapper.py resolve --tcid-list TC001,TC002,TC045 --test-dir tests/

  # Resolve from file
  python tcid_mapper.py resolve --tcid-file assigned_tcids.txt --test-dir tests/

  # Show feature grouping
  python tcid_mapper.py group --tcid-list TC001,TC002,TC045 --test-dir tests/

  # Full inventory
  python tcid_mapper.py inventory --test-dir tests/

  # Generate argfile for robot command
  python tcid_mapper.py generate-argfile --tcid-list TC001,TC045 --test-dir tests/ --output run.args
  robot --argumentfile run.args tests/
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Common arguments
    def add_common_args(p):
        p.add_argument("--test-dir", required=True,
            help="Root directory containing test scripts")
        p.add_argument("--format", choices=["text", "json"], default="text",
            help="Output format (default: text)")

    def add_tcid_args(p):
        p.add_argument("--tcid-list", default="",
            help="Comma-separated TCIDs")
        p.add_argument("--tcid-file", default=None,
            help="File with TCIDs (1 per line)")

    # ── resolve ──
    p_resolve = subparsers.add_parser("resolve",
        help="Resolve TCIDs to .robot file paths")
    add_common_args(p_resolve)
    add_tcid_args(p_resolve)

    # ── group ──
    p_group = subparsers.add_parser("group",
        help="Show feature grouping for TCIDs")
    add_common_args(p_group)
    add_tcid_args(p_group)

    # ── inventory ──
    p_inv = subparsers.add_parser("inventory",
        help="Show full test inventory")
    add_common_args(p_inv)

    # ── generate-argfile ──
    p_arg = subparsers.add_parser("generate-argfile",
        help="Generate Robot Framework arguments file")
    add_common_args(p_arg)
    add_tcid_args(p_arg)
    p_arg.add_argument("--output", default=None,
        help="Output argfile path (default: stdout)")

    # ── resolve-folders ──
    p_folders = subparsers.add_parser("resolve-folders",
        help="Resolve folder paths to .robot files and generate argfile")
    add_common_args(p_folders)
    p_folders.add_argument("--folder-list", default="",
        help="Comma-separated folder paths (relative to test-dir)")
    p_folders.add_argument("--folder-file", default=None,
        help="File with folder paths (1 per line)")
    p_folders.add_argument("--output", default=None,
        help="Output argfile path (default: stdout)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "resolve": cmd_resolve,
        "group": cmd_group,
        "inventory": cmd_inventory,
        "generate-argfile": cmd_generate_argfile,
        "resolve-folders": cmd_resolve_folders,
    }

    try:
        exit_code = commands[args.command](args)
        sys.exit(exit_code or 0)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
