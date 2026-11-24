"""Exact compatibility / conflict analysis between templates.

Two templates *conflict* when there EXISTS an 8-digit body which:

1. matches both literal prefixes, and
2. makes the two templates decode to different ``(scale_no, net_weight_g)``.

Only conflicting pairs are forced to use different marker digits.  If every
shared body decodes identically under both templates, they may share a marker.

The analysis is exact (no brute force over 10**8 bodies):

* A common body exists iff the two prefixes agree on their overlap.
* Scale numbers are compared as strings (leading zeros are significant).
  For equal-length ranges the extracted characters can be forced equal by
  prefix-fixed digits; otherwise a free digit can always be chosen to make
  them differ.
* Net weight is an integer number of grams.  Its value is an affine form
  ``C + sum(c_j * digit_j)`` over body positions; it is equal for every
  common completion iff ``C == 0`` and every non-zero coefficient lands on a
  prefix-fixed digit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .domain import BODY_LENGTH, TemplateSpec


def prefixes_compatible(p1: str, p2: str) -> bool:
    """True iff some body can start with both prefixes."""
    n = min(len(p1), len(p2))
    return p1[:n] == p2[:n]


def fixed_digits(t: TemplateSpec) -> dict[int, int]:
    """Body positions fixed by the template prefix."""
    return {i: int(ch) for i, ch in enumerate(t.prefix)}


def common_fixed_digits(a: TemplateSpec, b: TemplateSpec) -> Optional[dict[int, int]]:
    """Merged fixed digits for a common body, or None if prefixes clash."""
    if not prefixes_compatible(a.prefix, b.prefix):
        return None
    fixed = fixed_digits(a)
    for pos, digit in fixed_digits(b).items():
        if pos in fixed and fixed[pos] != digit:
            return None
        fixed[pos] = digit
    return fixed


def scale_always_equal(a: TemplateSpec, b: TemplateSpec, fixed: dict[int, int]) -> bool:
    """Whether scale strings are equal for EVERY body consistent with ``fixed``."""
    ra, rb = a.scale, b.scale
    if ra.length != rb.length:
        # Different lengths -> strings can never be equal.
        return False
    for k in range(ra.length):
        pa, pb = ra.start + k, rb.start + k
        if pa == pb:
            continue
        da = fixed.get(pa)
        db = fixed.get(pb)
        if da is None or db is None:
            # At least one digit is free: it can be chosen to differ.
            return False
        if da != db:
            # Fixed to different digits: always differ.
            return False
    return True


def weight_always_equal(a: TemplateSpec, b: TemplateSpec, fixed: dict[int, int]) -> bool:
    """Whether integer gram values are equal for EVERY consistent body."""
    coeff = [0] * BODY_LENGTH

    la = a.weight.length
    for k in range(la):
        pos = a.weight.start + k
        coeff[pos] += 10 ** (la - 1 - k)

    lb = b.weight.length
    for k in range(lb):
        pos = b.weight.start + k
        coeff[pos] -= 10 ** (lb - 1 - k)

    constant = 0
    for pos, c in enumerate(coeff):
        if c == 0:
            continue
        if pos in fixed:
            constant += c * fixed[pos]
        else:
            # Free variable with a non-zero coefficient: changing its digit
            # changes the value, so equality cannot hold for all bodies.
            return False
    return constant == 0


@dataclass
class PairAnalysis:
    compatible_body: bool
    scale_can_differ: bool
    weight_can_differ: bool
    conflict: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "compatible_body": self.compatible_body,
            "scale_can_differ": self.scale_can_differ,
            "weight_can_differ": self.weight_can_differ,
            "conflict": self.conflict,
            "reason": self.reason,
        }


def analyze_pair(a: TemplateSpec, b: TemplateSpec) -> PairAnalysis:
    fixed = common_fixed_digits(a, b)
    if fixed is None:
        return PairAnalysis(
            compatible_body=False,
            scale_can_differ=False,
            weight_can_differ=False,
            conflict=False,
            reason="prefixes never match the same body",
        )

    scale_equal = scale_always_equal(a, b, fixed)
    weight_equal = weight_always_equal(a, b, fixed)
    scale_diff = not scale_equal
    weight_diff = not weight_equal
    conflict = scale_diff or weight_diff

    if conflict:
        parts = []
        if scale_diff:
            parts.append("scale")
        if weight_diff:
            parts.append("net_weight")
        reason = "same body may decode differently in: " + ", ".join(parts)
    else:
        reason = "all shared bodies decode identically"

    return PairAnalysis(True, scale_diff, weight_diff, conflict, reason)


def conflicts(a: TemplateSpec, b: TemplateSpec) -> bool:
    return analyze_pair(a, b).conflict


def build_adjacency(templates: list[TemplateSpec]) -> list[set[int]]:
    """Conflict graph adjacency (indices into ``templates``)."""
    n = len(templates)
    adj: list[set[int]] = [set() for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if conflicts(templates[i], templates[j]):
                adj[i].add(j)
                adj[j].add(i)
    return adj
