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
_PII_WORD = (
    r"(?:email|e_mail|phone|mobile|telephone|msisdn|ssn|social_security|"
    r"national_id|nationalid|passport|driver_?licen[cs]e|tax_?id|"
    r"credit_?card|card_?number|pan_?number|iban|account_?number|"
    r"date_?of_?birth|birth_?date|\bdob\b|"
    r"first_?name|last_?name|full_?name|sur_?name|given_?name|"
    r"home_?address|postal_?address|street_?address|\bpii\b)"
)
_PII_CONTEXT = re.compile(r"\w*" + _PII_WORD + r"\w*", re.IGNORECASE)

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


@check(
    id="NB-PII-TOKENISED", ref="5.5.4",
    title="**Sensitive data**: Masked/tokenized where required; format validation applied",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_pii_is_tokenised(ctx: CheckContext) -> Verdict:
    """PII passing through a notebook is hashed/masked, and its format is validated.

    Severity is High rather than the checklist's Medium because the failure is
    not recoverable by a later fix: once raw PII has been written to the lake it
    has to be found, purged and re-loaded, and every downstream copy chased.

    **Scope — the notebook half only.** ``WS-DDM`` (ref 6.2.3) covers Warehouse
    Dynamic Data Masking from column metadata. This check never looks at table
    metadata; it reads what the transformation code does before the value lands.

    **What it can determine.** Whether a notebook that names PII columns
    (email / phone / SSN / national id / card number / date of birth / person
    name / address) applies a tokenisation call — ``sha2``, ``md5``, ``mask…``,
    ``tokenize``, ``encrypt``, a ``regexp_replace`` to ``***`` — near those
    columns, and whether it validates their format (a regex/``rlike``, a length
    or pattern test).

    **What it cannot.** Judge whether the *right* columns were masked, whether
    the hash is salted, or whether masking happens in a stored procedure or a
    view instead. Proximity is what links the control to the column, so a
    notebook that masks in one function and names PII in another may be judged
    conservatively; that is a deliberate bias — over-crediting masking is the
    dangerous error here.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not _PII_CONTEXT.search(code):
        return not_applicable("Notebook names no personal-data column (email, phone, "
                              "national id, card number, name, address …)")

    tokenised = _near_pii(_TOKENISATION, code)
    validated = _near_pii(_FORMAT_VALIDATION, code)
    if tokenised and validated:
        return graded(3, "Personal-data columns are hashed/masked/tokenised and their "
                         "format is validated before use")
    if tokenised:
        return graded(2, "Personal-data columns are hashed/masked/tokenised, but no format "
                         "validation (regex/pattern/length) is applied to them")
    if validated:
        return graded(1, "Personal-data columns are format-validated but never masked or "
                         "tokenised — the raw values are written as they arrived")
    return graded(0, "Personal-data columns are handled with no masking, tokenisation or "
                     "format validation — raw PII is carried through unchanged")
