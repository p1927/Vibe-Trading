"""Tests for src.quantlib.fundmath.

Two rules the assertions here follow.

*Roots are checked against a construction, not against the solver.* The XIRR
tests build a schedule whose net present value at a chosen rate is zero by
arithmetic, then ask the solver to find that rate back. The annual-spacing test
goes further and cross-checks against a polynomial root found by ``np.roots``,
which shares no code with the module under test.

*The waterfall example is worked by hand in the docstring of its test.* Every
tier figure is derived on paper first, so a wrong implementation cannot make the
test agree with itself.
"""

import datetime as dt

import numpy as np
import pytest

from src.entities.cashflow import CashFlow, CashFlowSeries
from src.quantlib.fundmath import (
    CONTRIBUTION_KINDS,
    TIER_CARRY_SPLIT,
    TIER_CATCH_UP,
    TIER_PREFERRED_RETURN,
    TIER_RETURN_OF_CAPITAL,
    FundMultiples,
    NoSignChangeError,
    WaterfallResult,
    WaterfallTier,
    XIRRConvergenceError,
    XIRRError,
    distributed_capital,
    dpi,
    european_waterfall,
    fund_multiples,
    moic,
    npv,
    paid_in_capital,
    preferred_return_amount,
    residual_value,
    rvpi,
    tvpi,
    waterfall_split,
    xirr,
    xirr_all,
)

DAYS_PER_YEAR = 365.0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def make_series(rows, *, currency: str = "USD") -> CashFlowSeries:
    """Build a series from ``(date, amount, kind)`` triples."""
    return CashFlowSeries(
        tuple(CashFlow(date=d, amount=a, kind=k, currency=currency) for d, a, k in rows)
    )


def constructed_schedule(rate: float) -> CashFlowSeries:
    """A schedule whose XIRR is exactly ``rate``, by construction.

    Four irregularly dated inflows are chosen freely; the single opening outflow
    is then set to minus their present value at ``rate``, which forces the net
    present value at ``rate`` to zero.
    """
    start = dt.date(2019, 3, 15)
    inflows = [
        (dt.date(2019, 11, 2), 120.0),
        (dt.date(2021, 6, 30), 450.0),
        (dt.date(2022, 2, 14), 300.0),
        (dt.date(2024, 9, 1), 800.0),
    ]
    present_value = sum(
        amount / (1.0 + rate) ** ((day - start).days / DAYS_PER_YEAR)
        for day, amount in inflows
    )
    rows = [(start, -present_value, "purchase")]
    rows.extend((day, amount, "proceeds") for day, amount in inflows)
    return make_series(rows)


# --------------------------------------------------------------------------
# xirr — recovery of a known rate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rate", [-0.35, -0.05, 0.0, 0.0001, 0.1234, 0.85, 3.0])
def test_xirr_recovers_the_constructed_rate(rate):
    assert abs(xirr(constructed_schedule(rate)) - rate) < 1e-6


def test_xirr_recovery_is_far_tighter_than_the_required_tolerance():
    # The bracketed solver closes to machine precision, not just to 1e-6.
    assert xirr(constructed_schedule(0.1234)) == pytest.approx(0.1234, abs=1e-12)


def test_npv_at_the_solved_rate_is_zero():
    series = constructed_schedule(0.1234)
    assert npv(series, xirr(series)) == pytest.approx(0.0, abs=1e-9)


def test_guess_cannot_move_the_bracketed_answer():
    series = constructed_schedule(0.1234)
    assert xirr(series, guess=-0.9) == xirr(series, guess=50.0) == xirr(series)


# --------------------------------------------------------------------------
# xirr — annual dates must agree with the textbook IRR
# --------------------------------------------------------------------------


def test_annual_dates_agree_with_textbook_irr():
    """365-day spacing must reproduce the equal-period IRR.

    The reference is the real root of ``sum(cf_i * x**i) == 0`` with
    ``x = 1/(1+r)``, found by ``np.roots`` -- a polynomial solver that shares no
    code with the module under test.
    """
    amounts = [-1000.0, 300.0, 400.0, 500.0, 200.0]
    start = dt.date(2020, 1, 1)
    rows = [
        (
            start + dt.timedelta(days=365 * index),
            amount,
            "purchase" if amount < 0 else "proceeds",
        )
        for index, amount in enumerate(amounts)
    ]

    roots = np.roots(amounts[::-1])
    real_positive = [
        root.real for root in roots if abs(root.imag) < 1e-12 and root.real > 0
    ]
    assert len(real_positive) == 1
    textbook_irr = 1.0 / real_positive[0] - 1.0

    assert xirr(make_series(rows), days_per_year=365.0) == pytest.approx(
        textbook_irr, abs=1e-9
    )


def test_matches_the_published_excel_xirr_example():
    """Microsoft's documented XIRR worked example returns 0.373362535."""
    series = make_series(
        [
            ("2008-01-01", -10000.0, "purchase"),
            ("2008-03-01", 2750.0, "proceeds"),
            ("2008-10-30", 4250.0, "proceeds"),
            ("2009-02-15", 3250.0, "proceeds"),
            ("2009-04-01", 2750.0, "proceeds"),
        ]
    )
    assert xirr(series) == pytest.approx(0.373362535, abs=1e-8)


# --------------------------------------------------------------------------
# xirr — refusals
# --------------------------------------------------------------------------


def test_all_positive_series_raises_instead_of_returning_nonsense():
    series = make_series(
        [
            ("2020-01-01", 100.0, "distribution"),
            ("2021-01-01", 200.0, "distribution"),
            ("2022-01-01", 300.0, "distribution"),
        ]
    )
    with pytest.raises(NoSignChangeError):
        xirr(series)


def test_all_negative_series_raises():
    series = make_series(
        [
            ("2020-01-01", -100.0, "capital_call"),
            ("2021-01-01", -200.0, "capital_call"),
        ]
    )
    with pytest.raises(NoSignChangeError):
        xirr(series)


def test_no_sign_change_error_is_an_xirr_error_and_a_value_error():
    assert issubclass(NoSignChangeError, XIRRError)
    assert issubclass(XIRRError, ValueError)


def test_all_zero_series_raises():
    series = make_series(
        [("2020-01-01", 0.0, "distribution"), ("2021-01-01", 0.0, "distribution")]
    )
    with pytest.raises(NoSignChangeError):
        xirr(series)


def test_flows_on_a_single_date_raise():
    series = make_series(
        [("2020-01-01", -100.0, "capital_call"), ("2020-01-01", 150.0, "distribution")]
    )
    with pytest.raises(ValueError, match="not identifiable"):
        xirr(series)


def test_a_bare_list_of_floats_is_refused():
    with pytest.raises(TypeError, match="CashFlowSeries"):
        xirr([-1000.0, 400.0, 800.0])


def test_rate_at_or_below_minus_one_is_refused_by_npv():
    with pytest.raises(ValueError, match="greater than -1"):
        npv(constructed_schedule(0.1), -1.0)


# --------------------------------------------------------------------------
# xirr — valuation policy
# --------------------------------------------------------------------------


FUND_ROWS = [
    ("2019-01-01", -600.0, "capital_call"),
    ("2019-07-01", -400.0, "capital_call"),
    ("2021-01-01", 250.0, "nav"),
    ("2021-06-01", 500.0, "distribution"),
    ("2023-01-01", 900.0, "distribution"),
    ("2023-06-30", 300.0, "nav"),
]


def test_terminal_policy_uses_only_the_latest_mark():
    series = make_series(FUND_ROWS)
    explicit = make_series([row for row in FUND_ROWS if row[0] != "2021-01-01"])
    assert xirr(series) == pytest.approx(xirr(explicit, valuations="all"))


def test_exclude_policy_drops_every_mark():
    series = make_series(FUND_ROWS)
    cash_only = make_series([row for row in FUND_ROWS if row[2] != "nav"])
    assert xirr(series, valuations="exclude") == pytest.approx(
        xirr(cash_only, valuations="all")
    )


def test_including_residual_value_raises_the_irr():
    series = make_series(FUND_ROWS)
    assert xirr(series) > xirr(series, valuations="exclude")


def test_unknown_valuation_policy_is_refused():
    with pytest.raises(ValueError, match="valuations="):
        xirr(make_series(FUND_ROWS), valuations="latest")


def test_a_fund_with_no_distributions_still_has_an_irr_from_its_nav():
    series = make_series(
        [
            ("2020-01-01", -1000.0, "capital_call"),
            ("2023-01-01", 1500.0, "nav"),
        ]
    )
    years = (dt.date(2023, 1, 1) - dt.date(2020, 1, 1)).days / DAYS_PER_YEAR
    assert xirr(series) == pytest.approx(1.5 ** (1.0 / years) - 1.0, abs=1e-9)


def test_xirr_all_returns_a_single_root_for_a_conventional_schedule():
    assert xirr_all(constructed_schedule(0.2)) == pytest.approx([0.2], abs=1e-9)


def test_a_two_sign_change_schedule_reports_both_roots():
    """The textbook multiple-IRR pattern: out, in, out again.

    Two rates make the net present value zero, so reporting one of them as
    "the" IRR without saying so would be a hidden judgement call.
    """
    series = make_series(
        [
            ("2020-01-01", -100.0, "purchase"),
            ("2021-01-01", 230.0, "proceeds"),
            ("2022-01-01", -132.0, "purchase"),
        ]
    )
    roots = xirr_all(series)
    assert len(roots) == 2
    assert roots[0] < roots[1]
    for root in roots:
        assert npv(series, root) == pytest.approx(0.0, abs=1e-9)
    # Documented tie-break: the smallest root.
    assert xirr(series) == roots[0]


def test_a_root_outside_the_requested_window_is_not_returned():
    """The Newton fallback must not smuggle a root past the caller's bounds.

    The real root of this schedule is about 2.9%. Asked to search only above
    1000%, the scan finds nothing and Newton wanders back to 2.9% -- which is a
    genuine root but not one the caller asked for, so it is refused.
    """
    series = make_series(
        [("2019-03-15", -1428.0, "purchase"), ("2024-09-01", 1670.0, "proceeds")]
    )
    assert xirr(series) == pytest.approx(0.029026355, abs=1e-8)
    with pytest.raises(XIRRConvergenceError, match="not a root to tolerance"):
        xirr(series, rate_bounds=(10.0, 1.0e6))


# --------------------------------------------------------------------------
# multiples
# --------------------------------------------------------------------------


MULTIPLE_CASES = [
    FUND_ROWS,
    [
        ("2018-01-01", -1000.0, "capital_call"),
        ("2020-01-01", 1300.0, "distribution"),
    ],
    [
        ("2018-01-01", -250.0, "subscription"),
        ("2019-01-01", -750.0, "capital_call"),
        ("2022-01-01", 100.0, "dividend"),
        ("2023-01-01", 40.0, "proceeds"),
        ("2023-12-31", 1800.0, "nav"),
    ],
    [
        ("2021-01-01", -500.0, "capital_call"),
        ("2022-01-01", 120.0, "distribution"),
    ],
]


@pytest.mark.parametrize("rows", MULTIPLE_CASES)
def test_tvpi_is_exactly_dpi_plus_rvpi(rows):
    series = make_series(rows)
    assert tvpi(series) == dpi(series) + rvpi(series)


@pytest.mark.parametrize("rows", MULTIPLE_CASES)
def test_the_bundle_reports_the_same_identity(rows):
    result = fund_multiples(make_series(rows))
    assert isinstance(result, FundMultiples)
    assert result.tvpi == result.dpi + result.rvpi


@pytest.mark.parametrize("rows", MULTIPLE_CASES)
def test_the_bundle_agrees_with_the_standalone_functions(rows):
    series = make_series(rows)
    result = fund_multiples(series)
    assert result.paid_in == pytest.approx(paid_in_capital(series))
    assert result.distributed == pytest.approx(distributed_capital(series))
    assert result.residual_value == pytest.approx(residual_value(series))
    assert result.dpi == pytest.approx(dpi(series))
    assert result.rvpi == pytest.approx(rvpi(series))
    assert result.tvpi == pytest.approx(tvpi(series))
    assert result.moic == pytest.approx(moic(series))


def test_the_multiples_of_a_hand_computed_fund():
    # 1000 drawn, 1400 returned, 300 still on the books.
    series = make_series(
        [
            ("2018-01-01", -600.0, "capital_call"),
            ("2018-07-01", -400.0, "capital_call"),
            ("2021-01-01", 500.0, "distribution"),
            ("2022-01-01", 900.0, "distribution"),
            ("2022-12-31", 300.0, "nav"),
        ]
    )
    result = fund_multiples(series)
    assert result.paid_in == pytest.approx(1000.0)
    assert result.distributed == pytest.approx(1400.0)
    assert result.residual_value == pytest.approx(300.0)
    assert result.dpi == pytest.approx(1.4)
    assert result.rvpi == pytest.approx(0.3)
    assert result.tvpi == pytest.approx(1.7)
    assert result.moic == pytest.approx(1.7)


def test_moic_equals_tvpi_on_paid_in_and_diverges_on_invested_capital():
    series = make_series(MULTIPLE_CASES[2])
    assert moic(series) == pytest.approx(tvpi(series))
    # Deal-level denominator: only the 750 actually deployed.
    assert moic(series, invested_capital=750.0) == pytest.approx(1940.0 / 750.0)


def test_only_the_latest_mark_counts_as_residual_value():
    # Two marks; summing them would report 550 instead of 300.
    assert residual_value(make_series(FUND_ROWS)) == pytest.approx(300.0)


def test_a_liquidated_fund_has_no_residual_value():
    series = make_series(
        [("2018-01-01", -100.0, "capital_call"), ("2020-01-01", 130.0, "distribution")]
    )
    assert residual_value(series) == 0.0
    assert rvpi(series) == 0.0
    assert tvpi(series) == pytest.approx(dpi(series))


def test_contribution_kinds_are_a_parameter_not_a_hardcoded_set():
    series = make_series(
        [
            ("2020-01-01", -1000.0, "capital_call"),
            ("2020-06-01", -20.0, "fee"),
            ("2023-01-01", 1200.0, "distribution"),
        ]
    )
    assert paid_in_capital(series) == pytest.approx(1000.0)
    assert paid_in_capital(
        series, contribution_kinds=(*CONTRIBUTION_KINDS, "fee")
    ) == pytest.approx(1020.0)


def test_a_fund_with_no_capital_drawn_has_no_multiple():
    series = make_series([("2020-01-01", 100.0, "distribution")])
    with pytest.raises(ValueError, match="undefined"):
        dpi(series)


def test_a_negative_mark_is_refused_rather_than_guessed():
    series = CashFlowSeries(
        (
            CashFlow("2020-01-01", -100.0, "capital_call", "USD"),
            CashFlow("2021-01-01", -5.0, "nav", "USD"),
        )
    )
    with pytest.raises(ValueError, match="non-negative"):
        residual_value(series)


def test_multiples_refuse_a_bare_list():
    with pytest.raises(TypeError, match="CashFlowSeries"):
        tvpi([-100.0, 150.0])


# --------------------------------------------------------------------------
# waterfall — worked by hand
# --------------------------------------------------------------------------


def test_the_worked_european_waterfall_example():
    """Hand-worked 8% pref / 20% carry / 100% catch-up on 150 distributable.

    Contributed capital 100, preferred owed 8, carry 20%, catch-up 100%::

        tier 1  return of capital   100 -> LP 100                remaining 50
        tier 2  preferred return      8 -> LP   8                remaining 42
        tier 3  GP catch-up          C  -> GP   C
                C solves 1.00*C == 0.20*(8 + C)  =>  C = 1.6/0.8 = 2
                                       2 -> GP   2               remaining 40
        tier 4  carry split          40 -> GP 0.20*40 = 8, LP 32 remaining  0

        LP total = 100 + 8 + 0 + 32 = 140
        GP total =   0 + 0 + 2 +  8 =  10
        140 + 10 = 150, the whole distributable amount.

    Profit distributed is 150 - 100 = 50, of which the GP took 10, i.e. exactly
    the 20% carry rate -- which is what a completed catch-up is for.
    """
    result = waterfall_split(
        150.0, 100.0, 8.0, carry_rate=0.20, catch_up_rate=1.00
    )

    assert result.tier_amount(TIER_RETURN_OF_CAPITAL) == pytest.approx(100.0)
    assert result.tier_amount(TIER_PREFERRED_RETURN) == pytest.approx(8.0)
    assert result.tier_amount(TIER_CATCH_UP) == pytest.approx(2.0)
    assert result.tier_amount(TIER_CARRY_SPLIT) == pytest.approx(40.0)

    assert result.lp_total == pytest.approx(140.0)
    assert result.gp_total == pytest.approx(10.0)
    assert result.lp_total + result.gp_total == pytest.approx(150.0, abs=1e-12)
    assert sum(tier.amount for tier in result.tiers) == pytest.approx(
        150.0, abs=1e-12
    )
    assert result.gp_profit_share == pytest.approx(0.20)
    assert result.unreturned_capital == 0.0
    assert result.unpaid_preferred == 0.0


def test_a_partial_catch_up_rate_still_lands_the_gp_on_its_carry():
    """50% catch-up, 20% carry, pref 8, contributed 100, distributable 200.

        tier 1  return of capital  100                remaining 100
        tier 2  preferred return     8                remaining  92
        tier 3  catch-up  C solves 0.50*C == 0.20*(8 + C) => C = 1.6/0.3
                C = 5.333333...  -> GP 2.666667, LP 2.666667   remaining 86.666667
        tier 4  carry split 86.666667 -> GP 17.333333, LP 69.333333

        GP total = 2.666667 + 17.333333 = 20 = 20% of the 100 of profit.
        LP total = 100 + 8 + 2.666667 + 69.333333 = 180.
    """
    result = waterfall_split(200.0, 100.0, 8.0, carry_rate=0.20, catch_up_rate=0.50)
    assert result.tier_amount(TIER_CATCH_UP) == pytest.approx(1.6 / 0.3)
    assert result.gp_total == pytest.approx(20.0)
    assert result.lp_total == pytest.approx(180.0)
    assert result.gp_profit_share == pytest.approx(0.20)


def test_no_catch_up_tier_leaves_the_gp_below_its_nominal_carry():
    """A hard hurdle: 20% carry applies only above the 8 of preferred.

    100 returned, 8 preferred to LPs, then 92 split 20/80 -> GP 18.4, LP 73.6.
    GP takes 18.4 of the 100 of profit, i.e. 18.4%, not 20%.
    """
    result = waterfall_split(200.0, 100.0, 8.0, carry_rate=0.20, catch_up_rate=0.0)
    assert result.tier_amount(TIER_CATCH_UP) == 0.0
    assert result.gp_total == pytest.approx(18.4)
    assert result.lp_total == pytest.approx(181.6)
    assert result.gp_profit_share == pytest.approx(0.184)


def test_an_incomplete_catch_up_keeps_the_gp_short():
    """Only 109 distributable: the catch-up tier is truncated at 1 of its 2."""
    result = waterfall_split(109.0, 100.0, 8.0, carry_rate=0.20, catch_up_rate=1.0)
    assert result.tier_amount(TIER_CATCH_UP) == pytest.approx(1.0)
    assert result.tier_amount(TIER_CARRY_SPLIT) == 0.0
    assert result.gp_total == pytest.approx(1.0)
    assert result.lp_total == pytest.approx(108.0)
    assert result.gp_profit_share == pytest.approx(1.0 / 9.0)


def test_an_unmet_hurdle_pays_the_gp_nothing():
    result = waterfall_split(105.0, 100.0, 8.0, carry_rate=0.20, catch_up_rate=1.0)
    assert result.gp_total == 0.0
    assert result.lp_total == pytest.approx(105.0)
    assert result.unpaid_preferred == pytest.approx(3.0)
    assert result.unreturned_capital == 0.0


def test_capital_not_yet_returned_is_reported():
    result = waterfall_split(60.0, 100.0, 8.0, carry_rate=0.20, catch_up_rate=1.0)
    assert result.unreturned_capital == pytest.approx(40.0)
    assert result.unpaid_preferred == pytest.approx(8.0)
    assert result.lp_total == pytest.approx(60.0)
    assert result.gp_total == 0.0


@pytest.mark.parametrize(
    "distributable", [0.0, 1.0, 99.9, 100.0, 108.0, 110.0, 137.5, 1000.0]
)
@pytest.mark.parametrize("catch_up_rate", [0.0, 0.35, 0.5, 1.0])
def test_every_split_conserves_the_distributable_amount(distributable, catch_up_rate):
    result = waterfall_split(
        distributable, 100.0, 8.0, carry_rate=0.20, catch_up_rate=catch_up_rate
    )
    assert sum(tier.amount for tier in result.tiers) == pytest.approx(
        distributable, abs=1e-9
    )
    assert result.lp_total + result.gp_total == pytest.approx(
        distributable, abs=1e-9
    )


def test_waterfall_rate_validation():
    with pytest.raises(ValueError, match="carry_rate"):
        waterfall_split(150.0, 100.0, 8.0, carry_rate=1.0)
    with pytest.raises(ValueError, match="catch_up_rate"):
        waterfall_split(150.0, 100.0, 8.0, carry_rate=0.2, catch_up_rate=1.5)
    with pytest.raises(ValueError, match="never complete"):
        waterfall_split(150.0, 100.0, 8.0, carry_rate=0.2, catch_up_rate=0.1)
    with pytest.raises(ValueError, match="non-negative"):
        waterfall_split(-1.0, 100.0, 8.0, carry_rate=0.2)


def test_a_tier_that_does_not_conserve_cash_is_rejected_at_construction():
    with pytest.raises(ValueError, match="does not conserve cash"):
        WaterfallTier(name="bogus", amount=10.0, lp_amount=6.0, gp_amount=3.0)


def test_a_result_whose_tiers_do_not_reconcile_is_rejected():
    tiers = (
        WaterfallTier(
            name=TIER_RETURN_OF_CAPITAL, amount=100.0, lp_amount=100.0, gp_amount=0.0
        ),
    )
    with pytest.raises(ValueError, match="does not reconcile"):
        WaterfallResult(
            distributable=150.0,
            contributed_capital=100.0,
            preferred_amount=8.0,
            tiers=tiers,
            lp_total=100.0,
            gp_total=0.0,
            unreturned_capital=0.0,
            unpaid_preferred=8.0,
        )


# --------------------------------------------------------------------------
# preferred return + the series-driven waterfall
# --------------------------------------------------------------------------


def test_simple_preferred_accrues_per_contribution_from_its_own_date():
    series = make_series(
        [
            ("2020-01-01", -100.0, "capital_call"),
            ("2021-01-01", -100.0, "capital_call"),
            ("2022-01-01", 0.0, "distribution"),
        ]
    )
    first_days = (dt.date(2022, 1, 1) - dt.date(2020, 1, 1)).days
    second_days = (dt.date(2022, 1, 1) - dt.date(2021, 1, 1)).days
    expected = 100.0 * 0.08 * (first_days / 365.0) + 100.0 * 0.08 * (
        second_days / 365.0
    )
    assert preferred_return_amount(
        series, rate=0.08, compounding="simple"
    ) == pytest.approx(expected)


def test_a_later_draw_owes_less_hurdle_than_a_day_one_draw():
    early = make_series(
        [("2020-01-01", -200.0, "capital_call"), ("2025-01-01", 0.0, "distribution")]
    )
    late = make_series(
        [
            ("2020-01-01", -100.0, "capital_call"),
            ("2023-01-01", -100.0, "capital_call"),
            ("2025-01-01", 0.0, "distribution"),
        ]
    )
    assert preferred_return_amount(late, rate=0.08) < preferred_return_amount(
        early, rate=0.08
    )


def test_compound_preferred_exceeds_simple_beyond_one_year():
    series = make_series(
        [("2020-01-01", -100.0, "capital_call"), ("2025-01-01", 0.0, "distribution")]
    )
    assert preferred_return_amount(series, rate=0.08) > preferred_return_amount(
        series, rate=0.08, compounding="simple"
    )


def test_a_zero_hurdle_accrues_nothing():
    series = make_series(
        [("2020-01-01", -100.0, "capital_call"), ("2025-01-01", 0.0, "distribution")]
    )
    assert preferred_return_amount(series, rate=0.0) == 0.0
    assert preferred_return_amount(series, rate=0.0, compounding="simple") == 0.0


def test_a_contribution_after_the_measurement_date_is_refused():
    series = make_series(
        [("2020-01-01", -100.0, "capital_call"), ("2025-01-01", -50.0, "capital_call")]
    )
    with pytest.raises(ValueError, match="accrue backwards"):
        preferred_return_amount(series, rate=0.08, as_of="2021-01-01")


def test_unknown_preferred_compounding_is_refused():
    series = make_series(
        [("2020-01-01", -100.0, "capital_call"), ("2025-01-01", 0.0, "distribution")]
    )
    with pytest.raises(ValueError, match="compounding="):
        preferred_return_amount(series, rate=0.08, compounding="continuous")


def test_the_series_driven_waterfall_matches_the_hand_computed_split():
    """100 called 2020-01-01, 150 distributed 2022-01-01, 8% simple pref, 20/100.

    2020 is a leap year, so the accrual spans 731 days::

        preferred = 100 * 0.08 * 731/365 = 16.021917808219176
        tier 1  return of capital  100                remaining 50
        tier 2  preferred          16.021918          remaining 33.978082
        tier 3  catch-up  C = 0.2*16.021918/0.8 = 4.005479  -> GP 4.005479
                                                     remaining 29.972603
        tier 4  carry split 29.972603 -> GP 5.994521, LP 23.978082

        GP total = 4.005479 + 5.994521 = 10 = 20% of the 50 of profit
        LP total = 150 - 10 = 140
    """
    series = make_series(
        [
            ("2020-01-01", -100.0, "capital_call"),
            ("2022-01-01", 150.0, "distribution"),
        ]
    )
    expected_preferred = 100.0 * 0.08 * (731.0 / 365.0)
    result = european_waterfall(
        series,
        preferred_rate=0.08,
        carry_rate=0.20,
        catch_up_rate=1.0,
        compounding="simple",
    )
    assert result.contributed_capital == pytest.approx(100.0)
    assert result.distributable == pytest.approx(150.0)
    assert result.preferred_amount == pytest.approx(expected_preferred)
    assert result.tier_amount(TIER_CATCH_UP) == pytest.approx(
        0.2 * expected_preferred / 0.8
    )
    assert result.gp_total == pytest.approx(10.0)
    assert result.lp_total == pytest.approx(140.0)
    assert result.lp_total + result.gp_total == pytest.approx(150.0, abs=1e-12)


def test_the_series_waterfall_distributes_realised_cash_by_default():
    series = make_series(
        [
            ("2020-01-01", -100.0, "capital_call"),
            ("2022-01-01", 40.0, "distribution"),
            ("2022-12-31", 200.0, "nav"),
        ]
    )
    realised = european_waterfall(series, preferred_rate=0.08, carry_rate=0.20)
    liquidated = european_waterfall(
        series, preferred_rate=0.08, carry_rate=0.20, include_residual=True
    )
    assert realised.distributable == pytest.approx(40.0)
    assert liquidated.distributable == pytest.approx(240.0)
    assert realised.gp_total == 0.0
    assert liquidated.gp_total > 0.0


def test_the_series_waterfall_refuses_a_bare_list():
    with pytest.raises(TypeError, match="CashFlowSeries"):
        european_waterfall([-100.0, 150.0], preferred_rate=0.08, carry_rate=0.2)
