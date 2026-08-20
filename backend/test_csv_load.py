#!/usr/bin/env python3
"""Test if AdminChecks.csv can be loaded and parsed."""

from pathlib import Path
from auditfast.services.external_checks_service import load_external_checks, ExternalCheckError

# Try loading from different paths
paths_to_try = [
    "AdminChecks.csv",
    "../AdminChecks.csv",
    Path(__file__).parent.parent / "AdminChecks.csv",
]

print("=" * 60)
print("Testing CSV Loading")
print("=" * 60)

for path in paths_to_try:
    print(f"\nTrying: {path}")
    try:
        results, warnings = load_external_checks(path)
        print(f"✅ SUCCESS! Loaded {len(results)} external checks")
        if warnings:
            print(f"⚠️  Warnings: {warnings}")
        if results:
            print(f"\nFirst check loaded:")
            r = results[0]
            print(f"  - Workspace: {r.workspace}")
            print(f"  - Check ID: {r.check_id}")
            print(f"  - Ref: {r.ref}")
            print(f"  - Status: {r.status}")
            print(f"  - Score: {r.score}")
            print(f"  - Source: {r.source}")
        break
    except ExternalCheckError as e:
        print(f"❌ Error: {e}")
    except FileNotFoundError as e:
        print(f"❌ File not found: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

print("\n" + "=" * 60)
