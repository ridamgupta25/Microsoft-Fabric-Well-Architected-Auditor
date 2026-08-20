"""Security · Data Prep — what the transformation code does with sensitive data.

One point today (ref 5.5.4): a notebook that handles PII should tokenise or mask
it, and validate the format of the fields whose shape is well known (email,
phone, national ID).

This is deliberately the *notebook-side* half of the point. The Warehouse side —
Dynamic Data Masking on sensitive columns — is already ``WS-DDM`` (ref 6.2.3),
which reads ``masking_function`` from column metadata. The two surfaces are
independent controls: DDM hides a value at query time from users who lack the
unmask permission, while hashing in the pipeline means the raw value never lands
in the lake at all. Neither substitutes for the other, and neither check can see
the other's evidence.
"""
from __future__ import annotations

import re

from auditfast.core.check._notebook import NOTEBOOK_LAYERS, executable_code
from auditfast.core.check.helpers import Verdict, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

#: Column-name words that mark a value as personal data. Kept to names whose
#: meaning is unambiguous — ``name`` alone is excluded because ``table_name``,
#: ``file_name`` and ``column_name`` are metadata, not people.
#: Terms that are personal data wherever they appear. These name a person or a
#: government/financial identifier and have no common technical meaning.
_PII_UNAMBIGUOUS = (
    r"(?:e_?mail|phone|msisdn|ssn|social_security|"
    r"national_id|nationalid|passport|driver_?licen[cs]e|tax_?id|"
    r"credit_?card|card_?number|pan_?number|iban|"
    r"date_?of_?birth|birth_?date|\bdob\b|\bpii\b)"
)

#: Terms that are personal data *only in the right company*. Each has a common,
#: entirely innocent technical meaning, and matching them alone produced the
#: bulk of this check's false positives on a real estate:
#:
#: * ``full_name`` matched ``source_table_full_name`` - a fully-qualified table
#:   name, ``lakehouse.schema.table``. 23 notebooks about company sites,
#:   inventory parts, GL vouchers and production tonnage were reported as
#:   carrying personal data.
#: * ``email`` matched the pattern string ``.*email.*`` in column-classification
#:   rules - including, pointedly, the notebooks whose job is to *find*
#:   email-shaped fields. They mention the word; they carry no address.
#: * ``mobile`` matched "Mobile Brokered Stone" (a product) and "Mobile" (a
#:   plant in Alabama).
#:
#: They therefore count only when a second, corroborating personal-data term
#: appears in the same notebook - a person's data rarely arrives alone.
_PII_AMBIGUOUS = (
    r"(?:mobile|telephone|"
    r"first_?name|last_?name|full_?name|sur_?name|given_?name|"
    r"home_?address|postal_?address|street_?address|account_?number)"
)

_PII_WORD = rf"(?:{_PII_UNAMBIGUOUS[3:-1]}|{_PII_AMBIGUOUS[3:-1]})"
#: A PII word counts only when it forms a **whole column-name segment**, not
#: when it is buried inside a longer identifier. The old pattern allowed up to
#: 12 arbitrary characters on either side, so ``full_name`` matched
#: ``source_table_full_name`` - a fully-qualified table name - and ``email``
#: matched the pattern string ``.*email.*`` in a column-classification rule.
#:
#: A qualifier is allowed on either side only when it is separated by ``_``:
#: ``customer_email`` and ``email_address`` are column names, while
#: ``source_table_full_name`` is not, because ``full_name`` there is the tail of
#: a longer compound whose head (``source_table``) says what it really is.
#: ``\w*`` after an underscore keeps genuine two-word columns without letting a
#: term float anywhere inside an identifier.
def _pii_context(word: str) -> re.Pattern:
    qualified = r"(?:\w+_)?" + word + r"(?:_\w+)?"
    return re.compile(
        r"""(?:
              ["'`]\s*""" + qualified + r"""\s*["'`]     # "customer_email"
            | \.\s*""" + qualified + r"""\b              # df.customer_email
            | \b""" + qualified + r"""\s*(?=[,)\]\s=])   # customer_email,
        )""",
        re.IGNORECASE | re.VERBOSE,
    )


_PII_CONTEXT = _pii_context(_PII_WORD)
_PII_STRONG_CONTEXT = _pii_context(_PII_UNAMBIGUOUS)
_PII_WEAK_CONTEXT = _pii_context(_PII_AMBIGUOUS)

#: The value is replaced by something irreversible or reversible-only-with-a-key.
#: Each alternative is a call, so a comment or a column named ``mask_flag``
#: cannot satisfy it — and the code is read with ``executable_code``, so a
#: commented-out ``sha2(...)`` is not a control either.
_TOKENISATION = re.compile(
    r"\bsha2\s*\(|\bsha1\s*\(|\bmd5\s*\(|\bhashlib\s*\.|\bhash\s*\(|\bxxhash64\s*\(|"
    r"\bmask\w*\s*\(|\btokeni[sz]e\w*\s*\(|\bredact\w*\s*\(|\bpseudonymi[sz]e\w*\s*\(|"
    r"\bencrypt\w*\s*\(|\baes_encrypt\s*\(|\bFernet\s*\(|"
    r"\bregexp_replace\s*\([^\n]{0,120}?\*{2,}",
    re.IGNORECASE,
)

#: The sensitive value is checked for the shape it is supposed to have — an
#: email with an ``@``, a phone with the right digit count, an SSN pattern.
_FORMAT_VALIDATION = re.compile(
    r"\.rlike\s*\(|\bregexp_extract\s*\(|\bregexp_like\s*\(|\bre\s*\.\s*(?:match|fullmatch|search)\s*\(|"
    r"\bLIKE\s+[\"'][^\"']*[%_@][^\"']*[\"']|"
    r"\bemail\w*[^\n]{0,80}?(?:@|\\\.|contains\s*\()|"
    r"\b(?:phone|mobile|ssn|iban|postcode|zip)\w*[^\n]{0,80}?(?:\\d\{|length\s*\(|len\s*\()",
    re.IGNORECASE,
)


def _near_pii(pattern: re.Pattern, code: str, window: int = 160) -> bool:
    """True when ``pattern`` fires within ``window`` characters of a PII-named token.

    Proximity, not proof: it shows the control and the sensitive column appear in
    the same piece of logic. Hashing a batch id three cells away from an email
    column must not read as tokenised PII, which a whole-notebook match would
    allow.
    """
    spans = [m.span() for m in _PII_CONTEXT.finditer(code)]
    if not spans:
        return False
    for match in pattern.finditer(code):
        start, end = match.span()
        for pii_start, pii_end in spans:
            if pii_start - window <= end and start <= pii_end + window:
                return True
    return False


def _masked_column_names(ctx: CheckContext) -> set[str]:
    """Lower-cased names of every column the Warehouse masks (``sys.columns``).

    Empty for a Lakehouse-only workspace, or where the SQL endpoint could not be
    read - which callers must treat as "no masking evidence", never as "nothing
    is masked".
    """
    masked: set[str] = set()
    for table in (ctx.workspace.tables or {}).values():
        if not isinstance(table, dict):
            continue
        for column in table.get("columns") or []:
            if isinstance(column, dict) and column.get("is_masked"):
                name = str(column.get("name") or "").strip().lower()
                if name:
                    masked.add(name)
    return masked


def _references_masked_column(code: str, masked: set[str]) -> bool:
    """True when the notebook references a column the warehouse already masks."""
    lowered = code.lower()
    return any(re.search(rf"\b{re.escape(name)}\b", lowered) for name in masked)


@check(
    id="NB-PII-TOKENISED", ref="5.5.4",
    title="**Sensitive data**: Masked/tokenized where required; format validation applied",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS,
    requires=[Resource.NOTEBOOK_DEFINITIONS, Resource.TABLE_SCHEMAS,
              Resource.TABLE_COLUMNS],
    required=True,
)
def notebook_pii_is_tokenised(ctx: CheckContext) -> Verdict:
    """PII passing through a notebook is hashed/masked, and its format is validated.

    Severity is High rather than the checklist's Medium because the failure is
    not recoverable by a later fix: once raw PII has been written to the lake it
    has to be found, purged and re-loaded, and every downstream copy chased.

    **Scope - the notebook half only.** ``WS-DDM`` (ref 6.2.3) covers Warehouse
    Dynamic Data Masking from column metadata. This check reads what the
    transformation code does before the value lands - but it now *consults* the
    masking metadata, because a column already masked at the table level is
    protected whatever the notebook does, and failing a notebook for not masking
    it again would be wrong.

    **The trigger demands a column reference.** An earlier version matched any
    word merely containing a PII term, so a notebook standardising column names
    could hit on ``account_number`` in a rename list and be scored 0 - "raw PII
    is carried through unchanged" - while handling no personal data at all. That
    is a confident, alarming failure about something that is not happening.

    **Why the column name is the only signal.** Fabric exposes no column-level
    PII classification a delegated read-only tool can read: sensitivity labels
    are item-level and need the Power BI Scanner API with tenant admin, and
    Purview's column classifications live in a separate catalog behind a separate
    role. So this reads code, precisely, and says so.

    **What it can determine.** Whether a notebook that references PII columns
    (email / phone / SSN / national id / card number / date of birth / person
    name / address) applies a tokenisation call - ``sha2``, ``md5``, ``mask…``,
    ``tokenize``, ``encrypt``, a ``regexp_replace`` to ``***`` - near those
    columns, and whether it validates their format.

    **What it cannot.** Judge whether the *right* columns were masked, whether
    the hash is salted, or whether masking happens in a stored procedure or a
    view instead. Proximity is what links the control to the column, so a
    notebook that masks in one function and names PII in another may be judged
    conservatively; that is a deliberate bias - over-crediting masking is the
    dangerous error here.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)

    # An unambiguous term (ssn, credit_card, date_of_birth) is personal data on
    # its own. An ambiguous one (phone, mobile, full_name, account_number) has a
    # common technical meaning and needs corroboration: "Mobile" was a plant in
    # Alabama, "full_name" was a fully-qualified table name, and "email" was a
    # column-matching pattern in the notebooks whose job is to find email fields.
    strong = _PII_STRONG_CONTEXT.search(code)
    weak = _PII_WEAK_CONTEXT.findall(code)
    if not strong and len(weak) < 2:
        return not_applicable(
            "Notebook references no personal-data column. A term that is also "
            "ordinary technical vocabulary - phone, mobile, full_name, "
            "account_number - is not treated as personal data on its own, "
            "because a fully-qualified table name or a plant called Mobile is "
            "not someone's data"
        )

    tokenised = _near_pii(_TOKENISATION, code)
    validated = _near_pii(_FORMAT_VALIDATION, code)
    if tokenised and validated:
        return graded(3, "Personal-data columns are hashed/masked/tokenised and their "
                         "format is validated before use")
    if tokenised:
        return graded(2, "Personal-data columns are hashed/masked/tokenised, but no format "
                         "validation (regex/pattern/length) is applied to them")

    # A column the Warehouse already masks is protected regardless of what this
    # notebook does. The crawl reads `is_masked` from `sys.columns`; without
    # consulting it, a notebook reading an already-protected column would be
    # failed for not masking it a second time.
    masked_columns = _masked_column_names(ctx)
    if masked_columns and _references_masked_column(code, masked_columns):
        return graded(2, "Personal-data columns are not masked in this notebook, but the "
                         "column(s) it references carry Dynamic Data Masking at the "
                         "warehouse level, so the raw values are not exposed by default")

    if validated:
        return graded(1, "Personal-data columns are format-validated but never masked or "
                         "tokenised - the raw values are written as they arrived")
    return graded(1, "Personal-data columns are referenced with no masking, tokenisation "
                     "or format validation visible in this notebook. Masking applied in a "
                     "stored procedure, a view, or a downstream step is not readable here, "
                     "so confirm before treating this as unprotected")
