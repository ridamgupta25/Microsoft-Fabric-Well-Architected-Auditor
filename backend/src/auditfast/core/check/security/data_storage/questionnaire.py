"""Security · Data Storage — interactive (self-assessed) checks.

Points that cannot be read from Fabric but *can* be scored by asking the
reviewer to pick one option during the audit — the Azure Well-Architected Review
questionnaire model. The engine never runs these; the chosen answer is merged
into the score afterwards.
"""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-SEC-LABELS",
    ref="Q-SEC-1",
    title="Sensitivity labels applied to data holding sensitive information",
    pillar=Pillar.SECURITY,
    layers=(Layer.STORAGE, Layer.REPORTING),
    question=(
        "Are Microsoft Purview sensitivity labels applied to items that hold "
        "confidential or personal data, and how consistently?"
    ),
    options=(
        Option("enforced", "Mandatory labeling policy enforces labels tenant-wide", 3),
        Option(
            "most",
            "Applied to most sensitive items, but not enforced by policy",
            2,
            guidance="Turn on a mandatory/default sensitivity-labeling policy so new "
            "items are labeled automatically and labels cannot be removed.",
        ),
        Option(
            "some",
            "Applied ad hoc to a few items",
            1,
            guidance="Roll labels out to all workspaces holding sensitive data and "
            "add a default label policy.",
        ),
        Option(
            "none",
            "No sensitivity labels applied",
            0,
            guidance="Define a label taxonomy in Microsoft Purview and apply it to "
            "workspaces and items that hold confidential or personal data.",
        ),
    ),
)
