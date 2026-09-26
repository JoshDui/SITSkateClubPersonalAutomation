"""Refresh a rules registry from a local workbook. Dry-run unless --apply.

Requires the Function App's non-secret environment settings and an authorized
Azure CLI login. Does not print member names, handles, credentials, or IDs.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'functionapp'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('workbook')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    from shared.registry_import import parse_workbook, import_from_file
    rows = parse_workbook(args.workbook)
    summary = {
        'rows': len(rows),
        'eligible_handles': len({r['username'] for r in rows if r['valid'] and r['rules_acknowledged'] and not r['ambiguous']}),
        'invalid_excel_rows': [r['row'] for r in rows if not r['valid']],
        'ambiguous_excel_rows': [r['row'] for r in rows if r['valid'] and r['ambiguous']],
    }
    print(json.dumps(summary), flush=True)
    if args.apply:
        print(json.dumps(import_from_file(args.workbook)), flush=True)


if __name__ == '__main__':
    main()
