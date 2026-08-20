#!/usr/bin/env python
"""Quick test of external checks integration."""
import sys
import tempfile
from pathlib import Path

# Test 1: Import the new service module
print("Test 1: Importing external_checks_service...")
try:
    from auditfast.services.external_checks_service import (
        load_external_checks,
        ExternalCheckError,
    )
    print("✓ Successfully imported external_checks_service")
except ImportError as e:
    print(f"✗ Failed to import: {e}")
    sys.exit(1)

# Test 2: Check that CheckResult has source field
print("\nTest 2: Checking CheckResult has source field...")
try:
    from auditfast.core.models import CheckResult
    from auditfast.core.enums import Status, Pillar, Severity, Scope
    
    result = CheckResult(
        workspace="test-ws",
        workspace_role="Mixed",
        check_id="TEST-01",
        ref="1.1.1",
        title="Test Check",
        pillar=Pillar.SECURITY,
        scope=Scope.WORKSPACE,
        severity=Severity.HIGH,
        status=Status.PASS,
        score=3,
        evidence="Test evidence",
        recommendation="Test recommendation",
        source="external",  # This is the new field
    )
    assert result.source == "external"
    print("✓ CheckResult has source field and it works")
except Exception as e:
    print(f"✗ Failed: {e}")
    sys.exit(1)

# Test 3: Test CSV parsing with a sample file
print("\nTest 3: Testing CSV parsing...")
try:
    csv_content = """Workspace,Ref,Check ID,Status,Score,Title,Pillar
test-ws,1.1.1,SECURITY-001,NOT SCORED,,Security Check,Security & Access Control
test-ws,1.1.2,SECURITY-002,PASS,3,Another Check,Security & Access Control"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='') as f:
        f.write(csv_content)
        csv_path = f.name
    
    try:
        results, warnings = load_external_checks(csv_path, target_workspaces={"test-ws"})
        assert len(results) == 2
        assert results[0].source == "external"
        assert results[0].score is None  # NOT SCORED
        assert results[1].score == 3  # PASS
        print(f"✓ CSV parsing works (loaded {len(results)} checks)")
        if warnings:
            print(f"  Warnings: {warnings}")
    finally:
        Path(csv_path).unlink()
except Exception as e:
    print(f"✗ Failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 4: Check audit_service signature
print("\nTest 4: Checking audit_service.run_audit signature...")
try:
    from auditfast.services import audit_service
    import inspect
    
    sig = inspect.signature(audit_service.run_audit)
    params = list(sig.parameters.keys())
    
    assert "external_checks_csv" in params, f"external_checks_csv not in parameters: {params}"
    print("✓ audit_service.run_audit has external_checks_csv parameter")
except Exception as e:
    print(f"✗ Failed: {e}")
    sys.exit(1)

# Test 5: Check audit_runner signature
print("\nTest 5: Checking audit_runner.submit signature...")
try:
    from auditfast.services.audit_runner import AuditRunner
    import inspect
    
    sig = inspect.signature(AuditRunner.submit)
    params = list(sig.parameters.keys())
    
    assert "external_checks_csv" in params, f"external_checks_csv not in parameters: {params}"
    print("✓ AuditRunner.submit has external_checks_csv parameter")
except Exception as e:
    print(f"✗ Failed: {e}")
    sys.exit(1)

# Test 6: Check CLI has the flag
print("\nTest 6: Checking CLI has --external-checks flag...")
try:
    from auditfast import cli
    import io
    from contextlib import redirect_stdout, redirect_stderr
    
    parser = cli.build_parser()
    
    # Try parsing a command with the new flag
    try:
        args = parser.parse_args([
            "run",
            "--project", "config/project.example.yaml",
            "--external-checks", "AdminChecks.csv"
        ])
        assert args.external_checks == "AdminChecks.csv"
        print("✓ CLI has --external-checks flag and accepts it")
    except SystemExit:
        print("✗ CLI failed to parse --external-checks flag")
        sys.exit(1)
except Exception as e:
    print(f"✗ Failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 7: Check API schema has the field
print("\nTest 7: Checking AuditRequest schema...")
try:
    from auditfast.schemas.audit import AuditRequest
    
    # Try creating a request with the field
    req = AuditRequest(
        auth_session="test-session",
        external_checks_csv="/path/to/AdminChecks.csv"
    )
    assert req.external_checks_csv == "/path/to/AdminChecks.csv"
    print("✓ AuditRequest schema has external_checks_csv field")
except Exception as e:
    print(f"✗ Failed: {e}")
    sys.exit(1)

print("\n" + "=" * 60)
print("All integration tests passed! ✓")
print("=" * 60)
