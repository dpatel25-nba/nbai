"""Dependency-free outcome normalization for minutes-validity-contract-v1.

This component does not update targets or histories in production. Comments are
retained, never interpreted. The caller must supply externally approved mapping
or evidence-backed adjudication references; naming a reference is not approval.
All supplied durations are unsuperseded: corrections must be adjudicated upstream
with the old evidence and supersession decision retained in that audit trail.
"""
from dataclasses import dataclass
import math
import re
from typing import Any, Optional, Tuple


@dataclass(frozen=True)
class RawMinutes:
    value: Any
    source_id: str = ""
    evidence_refs: Tuple[str, ...] = ()
    comment: Any = None


@dataclass(frozen=True)
class ParticipationAssertion:
    value: str
    source_id: str = ""
    evidence_refs: Tuple[str, ...] = ()
    approval_ref: str = ""


@dataclass(frozen=True)
class NormalizedMinutes:
    actual_row_status: str
    minutes_parse_status: str
    participation_evidence: str
    minutes_status: str
    minutes_value: Optional[float]
    participated: Optional[bool]
    minutes_target_valid: bool
    evidence_refs: Tuple[str, ...]
    raw_evidence: Tuple[RawMinutes, ...]
    participation_assertions: Tuple[ParticipationAssertion, ...]
    raw_parse_statuses: Tuple[str, ...]
    ignored_assertion_indices: Tuple[int, ...]
    approval_refs_used: Tuple[str, ...]
    legacy_minutes: Any
    mapping_version: str = "minutes-validity-contract-v1"


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _refs(value):
    # A bare string is not a sequence of provenance references.
    return (isinstance(value, (tuple, list)) and bool(value)
            and all(_text(item) for item in value))


def _provenance(item):
    return _text(item.source_id) and _refs(item.evidence_refs)


def _missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


_CLOCK = re.compile(r"([0-9]+):([0-9]{2}(?:\.[0-9]+)?)")
_ISO = re.compile(r"PT(?:([0-9]+)M)?(?:([0-9]+(?:\.[0-9]+)?)S)?")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _parse(value):
    """Return status and finite nonnegative minutes; never clip or zero-fill."""
    if _missing(value):
        return "missing", None
    try:
        if isinstance(value, bool):
            return "malformed", None
        if isinstance(value, (int, float)):
            minutes = float(value)
        elif isinstance(value, str):
            value = value.strip()
            clock = _CLOCK.fullmatch(value)
            iso = _ISO.fullmatch(value)
            if clock or (iso and any(part is not None for part in iso.groups())):
                parts = (clock or iso).groups()
                seconds = float(parts[1] or 0)
                if not 0 <= seconds < 60:
                    return "malformed", None
                minutes = float(parts[0] or 0) + seconds / 60
            elif _NUMBER.fullmatch(value):
                minutes = float(value)
            else:
                return "malformed", None
        else:
            return "malformed", None
    except (ValueError, OverflowError):
        return "malformed", None
    if not math.isfinite(minutes) or minutes < 0:
        return "malformed", None
    # There is deliberately no regulation-time upper bound: allow overtime.
    return "valid", minutes


def normalize_minutes(raw_evidence=(), *, participation_assertions=(),
                      approved_references=(), legacy_minutes=None,
                      actual_row_present=True, completed=True):
    """Normalize one candidate/actual outcome, preserving all supplied evidence.

    Raw durations require a nonblank source and field/row references. Assertions
    additionally require an exact external approval allowlist match. Unsupported
    assertions are retained with their indices but have no semantic effect.
    A malformed observation remains flagged even alongside a usable duration.
    No conflict is resolved by order, majority, or an explicit numerical zero.
    Inputs must be RawMinutes/ParticipationAssertion records. Raw values may be
    arbitrary: malformed, negative, and nonfinite values produce invalid labels.
    The returned evidence records are retained verbatim, not modified or copied
    into a replacement raw schema.
    """
    observations = tuple(raw_evidence)
    assertions = tuple(participation_assertions)
    if any(not isinstance(item, RawMinutes) for item in observations):
        raise TypeError("raw_evidence must contain RawMinutes records")
    if any(not isinstance(item, ParticipationAssertion) for item in assertions):
        raise TypeError("participation_assertions must contain ParticipationAssertion records")
    if not isinstance(actual_row_present, bool) or not isinstance(completed, bool):
        raise TypeError("actual_row_present and completed must be booleans")
    if isinstance(approved_references, str):
        raise TypeError("approved_references must be a collection, not a string")
    approved = {ref for ref in approved_references if _text(ref)}
    statuses, durations = [], []
    for item in observations:
        status, value = _parse(item.value)
        if status != "missing" and not _provenance(item):
            status, value = "legacy_unverifiable", None
        statuses.append(status)
        if status == "valid":
            durations.append(value)

    # Keep per-observation statuses as well as the conservative aggregate flag.
    if "malformed" in statuses:
        parse_status = "malformed"
    elif "valid" in statuses:
        parse_status = "valid"
    elif "legacy_unverifiable" in statuses or legacy_minutes is not None:
        parse_status = "legacy_unverifiable"
    else:
        parse_status = "missing"

    claims, ignored, approvals, refs = set(), [], [], []
    for item in (*observations, *assertions):
        if isinstance(item.evidence_refs, (tuple, list)):
            refs.extend(ref for ref in item.evidence_refs if _text(ref))
    for index, item in enumerate(assertions):
        if (isinstance(item.value, str) and item.value in ("played", "dnp")
                and _provenance(item) and _text(item.approval_ref)
                and item.approval_ref in approved):
            claims.add(item.value)
            approvals.append(item.approval_ref)
        else:
            ignored.append(index)

    value, participated, participation = None, None, "unknown"
    if not completed or not actual_row_present:
        status = "not_yet_observed" if not completed else "absent_actual"
        parse_status = "not_applicable"
    elif (len(set(durations)) > 1 or len(claims) > 1
          or ("dnp" in claims and any(v > 0 for v in durations))):
        status, participation = "conflict", "conflict"
    elif "dnp" in claims:
        status, value, participated, participation = "verified_dnp", 0.0, False, "dnp"
    else:
        if durations:
            status, value = "observed", durations[0]
        else:
            status = "uncertain"
        if "played" in claims or (value is not None and value > 0):
            participated, participation = True, "played"

    return NormalizedMinutes(
        actual_row_status="present" if actual_row_present else "absent",
        minutes_parse_status=parse_status,
        participation_evidence=participation,
        minutes_status=status,
        minutes_value=value,
        participated=participated,
        minutes_target_valid=status in ("observed", "verified_dnp"),
        evidence_refs=tuple(dict.fromkeys(refs)),
        raw_evidence=observations,
        participation_assertions=assertions,
        raw_parse_statuses=tuple(statuses),
        ignored_assertion_indices=tuple(ignored),
        approval_refs_used=tuple(dict.fromkeys(approvals)),
        legacy_minutes=legacy_minutes,
    )
