"""Verify app.conflicts against brute-force enumeration of matching bodies."""

import itertools
import random

import pytest

from app.conflicts import analyze_pair
from app.domain import Range, TemplateSpec


def brute_conflict(a: TemplateSpec, b: TemplateSpec) -> bool:
    """Enumerate every body consistent with the merged prefixes."""
    fixed = {}
    for i, ch in enumerate(a.prefix):
        fixed[i] = ch
    for i, ch in enumerate(b.prefix):
        if i in fixed and fixed[i] != ch:
            return False  # no common body
        fixed[i] = ch

    free = [i for i in range(8) if i not in fixed]
    # Tests use prefixes long enough that free <= 4 -> <= 10k bodies.
    assert len(free) <= 4
    for combo in itertools.product("0123456789", repeat=len(free)):
        chars = ["0"] * 8
        for i, ch in fixed.items():
            chars[i] = ch
        for pos, ch in zip(free, combo):
            chars[pos] = ch
        body = "".join(chars)
        if not (a.matches(body) and b.matches(body)):
            continue
        if a.decode_parts(body) != b.decode_parts(body):
            return True
    return False


def _rand_tpl(rng: random.Random) -> TemplateSpec:
    plen = rng.randint(4, 8)  # enough fixed positions for exhaustive check
    prefix = "".join(str(rng.randint(0, 9)) for _ in range(plen))

    def rng_range():
        start = rng.randint(0, 7)
        length = rng.randint(1, min(4, 8 - start))
        return Range(start, length)

    return TemplateSpec(
        prefix=prefix, scale=rng_range(), weight=rng_range(), marker="1"
    )


@pytest.mark.parametrize("seed", range(200))
def test_conflict_analysis_matches_exhaustive_bruteforce(seed):
    rng = random.Random(seed)
    a = _rand_tpl(rng)
    b = _rand_tpl(rng)
    analysis = analyze_pair(a, b)
    expected = brute_conflict(a, b)
    assert analysis.conflict == expected, (
        f"seed {seed}: analysis={analysis.conflict} brute={expected} "
        f"prefixes={a.prefix!r},{b.prefix!r} "
        f"scale={a.scale},{b.scale} weight={a.weight},{b.weight} "
        f"reason={analysis.reason}"
    )
