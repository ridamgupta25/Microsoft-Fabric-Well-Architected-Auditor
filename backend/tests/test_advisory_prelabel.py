"""Tests for the deterministic advisory pre-labeler.

These pin the mechanical labelers so a script can never silently mislabel: each
case asserts the exact label produced from a small synthetic job object.
"""
from __future__ import annotations

import json

from tools.advisory_prelabel import (
    is_unreadable,
    label_generic,
    label_pl_deadletter,
    label_pl_failure_alert,
    label_pl_idempotent,
    label_pl_notify,
    label_pl_secrets,
    walk_activities,
)


def _obj(activities: list[dict]) -> dict:
    return {"facts": json.dumps({"properties": {"activities": activities}})}


def test_walk_descends_into_if_branches():
    activities = [
        {"name": "gate", "type": "IfCondition", "typeProperties": {
            "ifFalseActivities": [{"name": "inner", "type": "Office365Email"}]}}
    ]
    names = {a["name"] for a in walk_activities(activities)}
    assert names == {"gate", "inner"}


def test_notify_email_present_is_notifies():
    obj = _obj([{"name": "Alert", "type": "Office365Email",
                 "dependsOn": [{"activity": "Load", "dependencyConditions": ["Failed"]}]}])
    label, _reason, confidence = label_pl_notify(obj)
    assert label == "notifies"
    assert confidence == "high"


def test_notify_email_nested_in_if_is_found():
    obj = _obj([{"name": "gate", "type": "IfCondition", "typeProperties": {
        "ifFalseActivities": [{"name": "Alert", "type": "Office365Email"}]}}])
    assert label_pl_notify(obj)[0] == "notifies"


def test_notify_no_notifier_is_silent():
    obj = _obj([{"name": "Copy", "type": "Copy"}, {"name": "Lookup", "type": "Lookup"}])
    label, _reason, confidence = label_pl_notify(obj)
    assert label == "silent"
    assert confidence == "high"


def test_notify_web_to_slack_is_notifies():
    obj = _obj([{"name": "Post", "type": "Web",
                 "typeProperties": {"url": "https://hooks.slack.com/services/x"}}])
    assert label_pl_notify(obj)[0] == "notifies"


def test_notify_web_to_unknown_url_defers():
    obj = _obj([{"name": "Post", "type": "Web",
                 "typeProperties": {"url": "https://example.internal/thing"}}])
    label, _reason, _conf = label_pl_notify(obj)
    assert label is None  # left for the agent to review


def test_notify_empty_pipeline_is_undetermined():
    label, _reason, _conf = label_pl_notify({"facts": "{}"})
    assert label == "undetermined"


def test_notify_teams_activity_is_notifies():
    obj = _obj([{"name": "Notify", "type": "Teams"}])
    assert label_pl_notify(obj)[0] == "notifies"


# -- PL-FAILURE-ALERT -------------------------------------------------------
def test_failure_alert_email_on_failed_edge_is_wired():
    obj = _obj([{"name": "Alert", "type": "Office365Email", "state": "Active",
                 "dependsOn": [{"activity": "Load", "dependencyConditions": ["Failed"]}]}])
    assert label_pl_failure_alert(obj)[0] == "wired_to_failure"


def test_failure_alert_email_only_on_success_is_not_on_failure_path():
    obj = _obj([{"name": "Ok", "type": "Office365Email", "state": "Active",
                 "dependsOn": [{"activity": "Load", "dependencyConditions": ["Succeeded"]}]}])
    assert label_pl_failure_alert(obj)[0] == "notifier_not_on_failure_path"


def test_failure_alert_disabled_notifier_is_no_notifier():
    obj = _obj([{"name": "Alert", "type": "Office365Email", "state": "Inactive",
                 "dependsOn": [{"activity": "Load", "dependencyConditions": ["Failed"]}]}])
    assert label_pl_failure_alert(obj)[0] == "no_notifier"


def test_failure_alert_no_notifier():
    obj = _obj([{"name": "Copy", "type": "Copy"}])
    assert label_pl_failure_alert(obj)[0] == "no_notifier"


# -- PL-SECRETS -------------------------------------------------------------
def test_secrets_key_vault_reference_is_no_secret():
    obj = {"facts": json.dumps({"password": "@{KeyVault.secretName}"})}
    assert label_pl_secrets(obj)[0] == "no_secret"


def test_secrets_literal_account_key_defers_for_review():
    obj = {"facts": 'connectionString "AccountKey=abcdefghijklmnopqrstuvwxyz0123456789=="'}
    assert label_pl_secrets(obj)[0] is None


def test_secrets_empty_is_no_secret():
    assert label_pl_secrets({"facts": "{}"})[0] == "no_secret"


# -- PL-DEADLETTER ----------------------------------------------------------
def test_deadletter_redirect_routes_bad_rows():
    obj = _obj([{"name": "Copy", "type": "Copy",
                 "typeProperties": {"redirectIncompatibleRowSettings": {"linkedServiceName": "x"}}}])
    assert label_pl_deadletter(obj)[0] == "routes_bad_rows"


def test_deadletter_plain_copy_drops_or_halts():
    obj = _obj([{"name": "Copy", "type": "Copy", "typeProperties": {"sink": {}}}])
    assert label_pl_deadletter(obj)[0] == "drops_or_halts"


def test_deadletter_no_copy_is_undetermined():
    obj = _obj([{"name": "nb", "type": "TridentNotebook"}])
    assert label_pl_deadletter(obj)[0] == "undetermined"


# -- PL-IDEMPOTENT ----------------------------------------------------------
def test_idempotent_upsert_is_rerunnable():
    obj = _obj([{"name": "Copy", "type": "Copy",
                 "typeProperties": {"sink": {"writeBehavior": "upsert"}}}])
    assert label_pl_idempotent(obj)[0] == "rerunnable"


def test_idempotent_truncate_then_insert_is_rerunnable():
    obj = _obj([{"name": "Copy", "type": "Copy",
                 "typeProperties": {"sink": {"writeBehavior": "insert"},
                                    "preCopyScript": "TRUNCATE TABLE dbo.t"}}])
    assert label_pl_idempotent(obj)[0] == "rerunnable"


def test_idempotent_plain_insert_appends_duplicates():
    obj = _obj([{"name": "Copy", "type": "Copy",
                 "typeProperties": {"sink": {"writeBehavior": "insert"}}}])
    assert label_pl_idempotent(obj)[0] == "appends_duplicates"


def test_idempotent_no_copy_is_undetermined():
    obj = _obj([{"name": "nb", "type": "TridentNotebook"}])
    assert label_pl_idempotent(obj)[0] == "undetermined"


# -- generic unreadable pre-pass --------------------------------------------
def test_generic_marks_unreadable_undetermined():
    assert label_generic({"facts": "definition not readable"})[0] == "undetermined"
    assert label_generic({"facts": ""})[0] == "undetermined"
    assert label_generic({"facts": "{}"})[0] == "undetermined"


def test_generic_defers_readable_object():
    obj = {"facts": "5 table(s): a, b\n2 measure(s):\n [a] X = SUM(a[v])"}
    assert label_generic(obj)[0] is None


def test_is_unreadable_does_not_flag_zero_measures():
    # a model with tables but no measures is readable - not 'unreadable'
    assert is_unreadable({"facts": "1 table(s): Prelim\n0 measure(s):"}) is False
