"""Operations & Reliability · Data Operations — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-OPS-DR",
    ref="Q-OPS-1",
    title="Disaster-recovery / restore plan documented and tested",
    pillar=Pillar.OPERATIONS,
    layers=(Layer.ANY,),
    question=(
        "Is there a documented disaster-recovery / restore plan for this workspace's "
        "critical data, and has it been tested?"
    ),
    options=(
        Option("tested", "Plan documented and restore tested within the last year", 3),
        Option(
            "documented",
            "Plan documented but never tested",
            1,
            guidance="Run a restore drill to prove the recovery time/point objectives "
            "(RTO/RPO) are actually achievable.",
        ),
        Option(
            "none",
            "No DR / restore plan",
            0,
            guidance="Document a recovery plan (backups, OneLake/source re-hydration, "
            "deployment redeploy) with target RTO/RPO and test it.",
        ),
    ),
)

questionnaire_check(
    id="Q-OPS-RUNBOOK",
    ref="Q-OPS-3",
    title="Operational runbooks documented for common failures",
    pillar=Pillar.OPERATIONS,
    layers=(Layer.ANY,),
    question=(
        "Are operational runbooks documented for common failure modes and incident "
        "response for this workspace's pipelines and jobs?"
    ),
    options=(
        Option("comprehensive", "Runbooks cover the common failures and are kept current", 3),
        Option(
            "partial",
            "Some runbooks exist but coverage is patchy or stale",
            1,
            guidance="Expand runbook coverage to the top recurring incidents and review "
            "them on a schedule.",
        ),
        Option(
            "none",
            "No runbooks",
            0,
            guidance="Document runbooks for the most common/most impactful failures, "
            "including who to page and the recovery steps.",
        ),
    ),
)
