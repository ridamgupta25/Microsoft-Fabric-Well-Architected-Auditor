#!/usr/bin/env python3
"""Debug CSV loading."""

import csv
import sys
from pathlib import Path

csv_path = Path("../AdminChecks.csv")

print("=" * 80)
print(f"Attempting to load: {csv_path.absolute()}")
print("=" * 80)

if not csv_path.exists():
    print(f"❌ File not found: {csv_path}")
    sys.exit(1)

try:
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        
        print(f"\n✅ File opened successfully")
        print(f"\nColumn headers ({len(reader.fieldnames or [])} total):")
        for i, col in enumerate(reader.fieldnames or [], 1):
            print(f"  {i:2d}. '{col}'")
        
        print(f"\nRequired columns: Workspace, Ref, Check ID, Status, Score, Title, Pillar")
        
        fieldnames = set(reader.fieldnames or [])
        required = {"Workspace", "Ref", "Check ID", "Status", "Score", "Title", "Pillar"}
        missing = required - fieldnames
        
        if missing:
            print(f"\n❌ Missing columns: {missing}")
        else:
            print(f"\n✅ All required columns present")
        
        print(f"\n\nFirst 3 rows:")
        print("-" * 80)
        
        for row_num, row in enumerate(reader, start=1):
            if row_num > 3:
                break
            print(f"\nRow {row_num}:")
            for col in required:
                val = row.get(col, "").strip()
                print(f"  {col:15s}: {val}")
            
            # Show optional columns too
            print(f"  Optional columns:")
            for col in ["Layer role", "Object", "Severity", "Evidence", "Source"]:
                val = row.get(col, "").strip()
                if val:
                    print(f"    {col:15s}: {val}")
        
        # Count total rows
        row_count = row_num
        print(f"\n{'=' * 80}")
        print(f"Total rows: {row_count}")
        print(f"{'=' * 80}\n")

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Now try with the actual service
print("\nAttempting to load with external_checks_service...")
print("=" * 80)

try:
    from auditfast.services.external_checks_service import load_external_checks, ExternalCheckError
    
    results, warnings = load_external_checks("../AdminChecks.csv")
    
    print(f"✅ SUCCESS!")
    print(f"  Loaded {len(results)} external checks")
    if warnings:
        print(f"  Warnings: {len(warnings)}")
        for w in warnings[:5]:  # Show first 5
            print(f"    - {w}")
    
    if results:
        print(f"\nFirst check loaded:")
        r = results[0]
        print(f"  Workspace: {r.workspace}")
        print(f"  Check ID: {r.check_id}")
        print(f"  Ref: {r.ref}")
        print(f"  Title: {r.title}")
        print(f"  Pillar: {r.pillar}")
        print(f"  Status: {r.status}")
        print(f"  Score: {r.score}")
        print(f"  Source: {r.source}")
        print(f"  Evidence: {r.evidence}")

except ExternalCheckError as e:
    print(f"❌ CSV Load Error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Unexpected error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
