import itertools
import random

import pytest

from app.coloring import NoAssignment, solve_markers
from app.conflicts import conflicts
from app.domain import Range, TemplateSpec


def mk(prefix="", s=(2, 2), w=(4, 4), marker=None):
    return TemplateSpec(
        prefix=prefix, scale=Range(*s), weight=Range(*w), marker=marker
    )


def valid_coloring(old, new, chosen):
    temps = list(old) + [
        TemplateSpec(prefix=t.prefix, scale=t.scale, weight=t.weight, marker=m)
        for t, m in zip(new, chosen)
    ]
    for i in range(len(temps)):
        for j in range(i + 1, len(temps)):
            if temps[i].marker == temps[j].marker and conflicts(temps[i], temps[j]):
                return False
    return True


def brute_min_fresh(old, new):
    """Exhaustive optimum for small batches."""
    old_set = {t.marker for t in old}
    best = None
    for cand in itertools.product("0123456789", repeat=len(new)):
        chosen = list(cand)
        if not valid_coloring(old, new, chosen):
            continue
        fresh = len({m for m in chosen if m not in old_set})
        if best is None or fresh < best:
            best = fresh
    return best


def test_empty_batch():
    assert solve_markers([mk(marker="1")], []) == []


def test_clique_of_three_needs_three_digits():
    # Empty prefix + distinct scale lengths => pairwise conflict.
    new = [mk(s=(0, 1)), mk(s=(0, 2)), mk(s=(0, 3))]
    chosen = solve_markers([], new)
    assert len(set(chosen)) == 3
    assert valid_coloring([], new, chosen)


def test_new_template_blocked_from_two_old_markers_uses_zero_fresh_if_another_old():
    old = [
        mk(prefix="1", marker="1"),
        mk(prefix="2", marker="2"),
        mk(prefix="3", marker="3"),
    ]
    # Empty prefix, scale length 4 vs old length 2 -> conflicts with all olds
    # via different scale length... that blocks 1,2,3 but 0 fresh? 0 is fresh.
    new = [mk(prefix="", s=(0, 4))]
    chosen = solve_markers(old, new)
    assert chosen == ["0"]  # smallest fresh digit
    assert valid_coloring(old, new, chosen)


def test_can_reuse_old_marker_when_safe():
    old = [mk(prefix="1", marker="1"), mk(prefix="2", marker="2")]
    # prefix 1x clashes with prefix 2 and is decode-equal to old marker1 tpl
    new = [mk(prefix="19")]
    chosen = solve_markers(old, new)
    assert chosen == ["1"]


def test_blocked_from_all_ten_markers_has_no_solution():
    old = []
    for d in "0123456789":
        # empty-prefix? Need new template conflict with each old. Use same
        # empty prefix but different scale length => conflicts with all.
        old.append(mk(prefix=d, marker=d, s=(2, 2)))
    # prefix "" compatible with every single-digit prefix; scale length differs
    new = [mk(prefix="", s=(0, 1))]
    with pytest.raises(NoAssignment):
        solve_markers(old, new)


def test_preserves_old_markers_exactly():
    old = [mk(prefix="1", marker="7"), mk(prefix="2", marker="8")]
    new = [mk(prefix="3")]
    solve_markers(old, new)
    assert [t.marker for t in old] == ["7", "8"]


@pytest.mark.parametrize("seed", range(30))
def test_matches_brute_force_optimum_random(seed):
    rng = random.Random(seed)
    n_old = rng.randint(0, 3)
    n_new = rng.randint(1, 5)

    def rand_tpl(marker=None):
        plen = rng.randint(0, 3)
        prefix = "".join(str(rng.randint(0, 9)) for _ in range(plen))
        ss = rng.randint(0, 5)
        sl = rng.randint(1, min(3, 8 - ss))
        ws = rng.randint(0, 8 - 1)
        wl = rng.randint(1, min(3, 8 - ws))
        return mk(prefix=prefix, s=(ss, sl), w=(ws, wl), marker=marker)

    markers = rng.sample("0123456789", n_old)
    old = [rand_tpl(m) for m in markers]
    new = [rand_tpl() for _ in range(n_new)]

    chosen = solve_markers(old, new)
    assert valid_coloring(old, new, chosen)

    expected = brute_min_fresh(old, new)
    old_set = set(markers)
    got = len({m for m in chosen if m not in old_set})
    if expected is None:
        pytest.skip("brute force found no coloring")
    assert got == expected
