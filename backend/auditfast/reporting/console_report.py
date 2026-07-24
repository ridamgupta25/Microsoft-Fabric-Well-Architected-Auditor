"""Console summary output (rich if available, plain text otherwise)."""
from __future__ import annotations

from ..core.models import PILLARS, Status
from ..core.scoring import rating


def _fmt(pct):
    return "  -  " if pct is None else f"{pct:5.1f}%"


def print_summary(project_name: str, agg: dict, mode: str) -> None:
    overall = agg["overall"]
    label, emoji = rating(overall)
    counts = agg["counts"]

    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()
        console.print()
        console.print(Panel.fit(
            f"[bold]{project_name}[/bold]\n"
            f"Overall: [bold]{_fmt(overall)}[/bold]  {emoji} {label}   "
            f"([green]{counts[Status.PASS]} pass[/green] / "
            f"[yellow]{counts[Status.PARTIAL]} partial[/yellow] / "
            f"[red]{counts[Status.FAIL]} fail[/red])",
            title=f"AuditFAST Core  ({mode} mode)", border_style="cyan"))

        table = Table(title="Pillar Scorecard", header_style="bold")
        table.add_column("Pillar")
        table.add_column("Score", justify="right")
        table.add_column("Rating")
        table.add_column("Checks", justify="right")
        for p in PILLARS:
            pv = agg["by_pillar"][p]
            lbl, em = rating(pv["pct"])
            table.add_row(p, _fmt(pv["pct"]), f"{em} {lbl}", str(pv["count"]))
        console.print(table)

        ws_table = Table(title="Per-Workspace", header_style="bold")
        ws_table.add_column("Workspace")
        ws_table.add_column("Layer role")
        ws_table.add_column("Score", justify="right")
        ws_table.add_column("Rating")
        for ws, wv in agg["by_workspace"].items():
            lbl, em = rating(wv["pct"])
            ws_table.add_row(ws, wv.get("role", ""), _fmt(wv["pct"]), f"{em} {lbl}")
        console.print(ws_table)
        console.print()
        return
    except Exception:
        pass

    # Plain fallback
    print()
    print(f"=== AuditFAST Core ({mode} mode) : {project_name} ===")
    print(f"Overall: {_fmt(overall)}  {label}   "
          f"({counts[Status.PASS]} pass / {counts[Status.PARTIAL]} partial / {counts[Status.FAIL]} fail)")
    print("\nPillar scorecard:")
    for p in PILLARS:
        pv = agg["by_pillar"][p]
        lbl, _ = rating(pv["pct"])
        print(f"  {p:26s} {_fmt(pv['pct'])}  {lbl:12s} ({pv['count']} checks)")
    print("\nPer-workspace:")
    for ws, wv in agg["by_workspace"].items():
        lbl, _ = rating(wv["pct"])
        print(f"  {ws:28s} [{wv.get('role','')}] {_fmt(wv['pct'])}  {lbl}")
    print()
