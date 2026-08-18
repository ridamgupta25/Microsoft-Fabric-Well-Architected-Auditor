"""Layer words must be found by token, never by ``\\b`` word boundary.

The bug these pin: ``\\bbronze\\b`` cannot match ``bronze_raw_orders``. The
character after ``bronze`` is ``_``, which *is* a word character, so there is no
boundary there. Three checks gated on that regex, which meant the ``layer_table``
naming convention - the most common one in the wild, and the one the checks exist
to look for - was the single convention they could not see.

Found on a real workspace: a notebook writing ``bronze_raw_orders`` reported
"Notebook does not write a Bronze/raw table" while ``write_targets`` on the same
string returned that very table.
"""
from __future__ import annotations

import re

from auditfast.core.check._notebook import layer_words_in, writes_layer


def test_the_boundary_regex_really_does_fail():
    """Pin the root cause, so nobody reintroduces it thinking it was fine."""
    assert not re.search(r"\bbronze\b", "bronze_raw_orders", re.IGNORECASE)
    assert "bronze" in layer_words_in("bronze_raw_orders")


def test_layer_words_are_found_in_every_common_spelling():
    for name, expected in (
        ("bronze_raw_orders", "bronze"),
        ("silver_dim_customer", "silver"),
        ("gold_fact_sales", "gold"),
        ("LH_Bronze", "bronze"),
        ("silver.dim_customer", "silver"),
        ("abfss://ws@onelake/Gold/sales", "gold"),
        ("staging_orders", "bronze"),
        ("curated_customer", "silver"),
    ):
        assert expected in layer_words_in(name), name


def test_a_substring_is_not_a_layer():
    """``golden_gate`` is not gold; ``sliver`` is not silver."""
    assert not layer_words_in("golden_gate_bridge")
    assert not layer_words_in("sliver_of_data")


def test_writes_layer_reads_the_write_target_not_the_whole_notebook():
    """A notebook that *reads* Bronze does not thereby *write* it."""
    reads_only = "df = spark.read.table('bronze_raw_orders')\ndf.write.saveAsTable('silver_clean')"
    assert writes_layer(reads_only, "bronze") == (False, "")
    assert writes_layer(reads_only, "silver")[0]


def test_writes_layer_sees_every_layer_a_notebook_writes():
    """Unlike ``medallion_layer`` this does not pick one winner.

    A notebook writing bronze, silver and gold writes all three; asking about
    one layer must not be answered by whichever the tie-break chose.
    """
    code = (
        "a.write.saveAsTable('bronze_raw_orders')\n"
        "b.write.saveAsTable('silver_dim_customer')\n"
        "c.write.saveAsTable('gold_fact_sales')\n"
    )
    for layer in ("bronze", "silver", "gold"):
        found, target = writes_layer(code, layer)
        assert found, layer
        assert layer in target


def test_writes_layer_is_false_when_no_target_names_a_layer():
    code = "df.write.saveAsTable('sales_transactions')"
    assert writes_layer(code, "bronze") == (False, "")
