"""Exact hypergeometric coverage inversion: reference values and boundaries."""

import pytest

from tender_anchor.coverage_certificate import (
    ALPHA,
    CoverageError,
    as_fraction,
    hypergeometric_tail,
    invert_population_upper,
    make_certificate,
)


def test_tails_match_values_quoted_by_the_reviewer():
    public = invert_population_upper(overlap=40, seen_union=160, sample_a=100, sample_b=100)
    assert public["N_upper"] == 301
    assert float(public["tail_at_upper"]) == pytest.approx(0.052140334, abs=1e-9)
    assert float(public["tail_at_upper_plus_one"]) == pytest.approx(0.049205819, abs=1e-9)
    canonical = invert_population_upper(overlap=60, seen_union=140, sample_a=100, sample_b=100)
    assert canonical["N_upper"] == 185
    assert float(canonical["tail_at_upper"]) == pytest.approx(0.053388540, abs=1e-9)
    assert float(canonical["tail_at_upper_plus_one"]) == pytest.approx(
        0.045226241, abs=1e-9
    )


def test_upper_bound_is_exactly_the_largest_n_meeting_alpha():
    inversion = invert_population_upper(
        overlap=40, seen_union=160, sample_a=100, sample_b=100
    )
    upper = inversion["N_upper"]
    assert hypergeometric_tail(upper, 100, 100, 40) >= ALPHA
    assert hypergeometric_tail(upper + 1, 100, 100, 40) < ALPHA
    assert hypergeometric_tail(upper - 1, 100, 100, 40) >= ALPHA


def test_tail_is_monotone_and_bounded():
    previous = None
    for population in range(160, 400):
        tail = hypergeometric_tail(population, 100, 100, 40)
        assert 0 <= tail <= 1
        if previous is not None:
            assert tail <= previous
        previous = tail


def test_chapman_and_ratio_would_disagree_with_the_certificate():
    certificate = make_certificate(
        contract_id="operator_canonical_notices",
        unit="operator_canonical_notice",
        sampling_model="independent SRSWOR 100+100",
        m=60,
        U=140,
        sample_a=100,
        sample_b=100,
        threshold=as_fraction("3/4"),
        defects=[],
    )
    chapman_population = (101 * 101) / 61 - 1
    ratio = 60 / 200
    assert certificate["N_upper"] == 185
    # Chapman's point estimate (≈166) understates the one-sided uncertainty
    # bound (185); the raw recapture ratio (0.30) is not a coverage estimate.
    assert certificate["N_upper"] > chapman_population
    assert certificate["coverage_lower_bound"] == pytest.approx(140 / 185, abs=1e-9)
    assert certificate["coverage_lower_bound"] != pytest.approx(140 / chapman_population)
    assert ratio != pytest.approx(certificate["coverage_lower_bound"])
    assert certificate["decision"] == "certify"


def test_zero_recaptures_cannot_be_inverted():
    with pytest.raises(CoverageError):
        invert_population_upper(overlap=0, seen_union=160, sample_a=100, sample_b=100)
    certificate = make_certificate(
        contract_id="public_id_records",
        unit="public_id",
        sampling_model="independent SRSWOR 100+100",
        m=0,
        U=160,
        sample_a=100,
        sample_b=100,
        threshold=as_fraction("3/4"),
        defects=[],
    )
    assert certificate["decision"] == "reject"
    assert certificate["N_upper"] is None


def test_certificate_with_defects_never_certifies():
    certificate = make_certificate(
        contract_id="operator_canonical_notices",
        unit="operator_canonical_notice",
        sampling_model="independent SRSWOR 100+100",
        m=60,
        U=140,
        sample_a=100,
        sample_b=100,
        threshold=as_fraction("3/4"),
        defects=[{"code": "alias_conflict"}],
    )
    assert certificate["decision"] == "reject"
    assert certificate["certificate_issued"] is False
    assert "invalid_evidence" in certificate["reason_codes"]


def test_float_and_string_thresholds_are_exact():
    assert as_fraction(0.75) == as_fraction("3/4") == as_fraction("0.75")
    assert as_fraction(0.1) + as_fraction(0.2) == as_fraction("0.3")
    with pytest.raises(CoverageError):
        as_fraction("not-a-number")
    with pytest.raises(CoverageError):
        as_fraction(True)
