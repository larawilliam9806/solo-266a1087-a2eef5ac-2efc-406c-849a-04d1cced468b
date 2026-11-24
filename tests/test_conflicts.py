from app.conflicts import (
    analyze_pair,
    prefixes_compatible,
    scale_always_equal,
    weight_always_equal,
    common_fixed_digits,
)
from app.domain import Range, TemplateSpec


def tpl(prefix="", s=(2, 2), w=(4, 4), marker=None, name=""):
    return TemplateSpec(
        name=name,
        prefix=prefix,
        scale=Range(*s),
        weight=Range(*w),
        marker=marker,
    )


def test_prefix_compatibility():
    assert prefixes_compatible("12", "123")
    assert prefixes_compatible("123", "12")
    assert prefixes_compatible("", "9")
    assert not prefixes_compatible("12", "13")
    assert not prefixes_compatible("1", "2")


def test_identical_templates_do_not_conflict():
    a = tpl("10")
    b = tpl("10")
    r = analyze_pair(a, b)
    assert r.compatible_body and not r.conflict


def test_disjoint_prefixes_never_conflict_even_with_different_positions():
    a = tpl("1", s=(0, 4), w=(4, 4))
    b = tpl("2", s=(1, 3), w=(2, 2))
    r = analyze_pair(a, b)
    assert not r.compatible_body and not r.conflict


def test_different_scale_length_conflicts():
    a = tpl("1", s=(2, 2))
    b = tpl("1", s=(2, 3))
    assert analyze_pair(a, b).conflict


def test_scale_at_different_free_positions_can_differ():
    # prefix only fixes position 0; scale at 1 vs 2 are free and independent.
    a = tpl("1", s=(1, 1))
    b = tpl("1", s=(2, 1))
    fixed = common_fixed_digits(a, b)
    assert not scale_always_equal(a, b, fixed)
    assert analyze_pair(a, b).conflict


def test_prefix_forces_equal_scale_digits():
    # Both read position 1 and prefix fixes it to '7' -> always equal.
    a = tpl("17", s=(1, 1), w=(4, 2))
    b = tpl("17", s=(1, 1), w=(5, 2))
    fixed = common_fixed_digits(a, b)
    assert scale_always_equal(a, b, fixed)
    # weight ranges differ (4,6) vs (5,7): free positions -> conflict via weight
    assert analyze_pair(a, b).weight_can_differ


def test_fully_forced_equal_decode_has_no_conflict():
    # Same scale interval; weight A=[2,4), B=[2,4) identical too.
    a = tpl("12", s=(0, 2), w=(2, 2))
    b = tpl("1234", s=(0, 2), w=(2, 2))
    assert not analyze_pair(a, b).conflict


def test_weight_leading_zero_overlap_always_equal():
    # A weight body[2:4] = 0,d ; B weight body[3:4] = d and prefix fixes
    # body[2]='0' => integer grams always equal.
    a = tpl("120", s=(5, 2), w=(2, 2))
    b = tpl("120", s=(5, 2), w=(3, 1))
    fixed = common_fixed_digits(a, b)
    assert weight_always_equal(a, b, fixed)
    assert not analyze_pair(a, b).conflict


def test_weight_fixed_nonzero_makes_values_differ():
    # Same overlap setup but body[2] fixed to '5': A = 50+d, B = d.
    a = tpl("125", s=(5, 2), w=(2, 2))
    b = tpl("125", s=(5, 2), w=(3, 1))
    fixed = common_fixed_digits(a, b)
    assert not weight_always_equal(a, b, fixed)
    assert analyze_pair(a, b).conflict
