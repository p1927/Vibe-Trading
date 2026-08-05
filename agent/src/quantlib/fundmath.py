"""Private-markets fund mathematics on irregular cash flows.

This module is the analytics half of the asset spine: :mod:`src.entities.cashflow`
carries dated, signed, single-currency movements, and everything here turns such
a series into the numbers an LP or a fund controller actually reports.

Three things are deliberate.

**Nothing takes a bare list of floats.** Every public entry point takes a
:class:`~src.entities.cashflow.CashFlowSeries`. A list of floats loses the dates
(so an IRR silently becomes an equal-period IRR), loses the currency (so two
books get summed), and loses the kind (so a NAV mark gets added to realised
cash). All three were real defects; requiring the series makes them unstatable.

**A valuation is not cash.** Terminal NAV belongs in a fund IRR and in TVPI, and
must stay out of DPI. The ``valuations`` policy on :func:`xirr` and the separate
:func:`residual_value` keep that boundary explicit instead of leaving it to the
caller's filtering.

**No garbage roots.** :func:`xirr` refuses a series with no sign change rather
than returning whatever a Newton iteration wandered into, verifies the root it
does return, and exposes :func:`xirr_all` because a fund with recallable
distributions genuinely can have several. See :class:`XIRRError`.

Sign convention is inherited unchanged from the spine: ``amount > 0`` is cash
into the holder, so contributions are negative and distributions positive.
Functions that report a magnitude (``paid_in_capital``, ``distributed_capital``)
return a positive number and say so.
"""

from __future__ import annotations

import datetime as _dt
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq, newton

from src.entities.cashflow import CashFlow, CashFlowSeries
from src.entities.models import normalize_date

__all__ = [
    "CONTRIBUTION_KINDS",
    "DISTRIBUTION_KINDS",
    "VALUATION_POLICIES",
    "DEFAULT_VALUATION_POLICY",
    "DEFAULT_DAYS_PER_YEAR",
    "DEFAULT_RATE_BOUNDS",
    "DEFAULT_GRID_POINTS",
    "PREFERRED_COMPOUNDING",
    "DEFAULT_PREFERRED_COMPOUNDING",
    "TIER_RETURN_OF_CAPITAL",
    "TIER_PREFERRED_RETURN",
    "TIER_CATCH_UP",
    "TIER_CARRY_SPLIT",
    "XIRRError",
    "NoSignChangeError",
    "XIRRConvergenceError",
    "FundMultiples",
    "WaterfallTier",
    "WaterfallResult",
    "npv",
    "xirr",
    "xirr_all",
    "paid_in_capital",
    "distributed_capital",
    "residual_value",
    "dpi",
    "rvpi",
    "tvpi",
    "moic",
    "fund_multiples",
    "preferred_return_amount",
    "waterfall_split",
    "european_waterfall",
]

#: Kinds treated as capital going into the fund. A parameter everywhere it is
#: used -- a fund that charges management fees outside the commitment should
#: pass ``contribution_kinds=(*CONTRIBUTION_KINDS, "fee")``.
CONTRIBUTION_KINDS: tuple[str, ...] = ("contribution", "capital_call", "subscription")

#: Kinds treated as capital coming back out of the fund.
DISTRIBUTION_KINDS: tuple[str, ...] = (
    "distribution",
    "dividend",
    "redemption",
    "proceeds",
)

#: How :func:`npv` / :func:`xirr` treat valuation marks. ``"terminal"`` keeps
#: only the latest mark (the standard fund-IRR treatment: residual value is
#: assumed liquidated at the measurement date), ``"all"`` keeps every mark
#: (wrong for an IRR unless the series holds exactly one), ``"exclude"`` drops
#: them (a realised-cash-only IRR).
VALUATION_POLICIES: tuple[str, ...] = ("terminal", "all", "exclude")

#: Policy used when the caller does not name one.
DEFAULT_VALUATION_POLICY: str = "terminal"

#: Day basis for converting a date difference into years. 365 is the Excel
#: ``XIRR`` convention and the private-markets norm.
DEFAULT_DAYS_PER_YEAR: float = 365.0

#: Rate window scanned for roots, as ``(low, high)``. The low bound must stay
#: above ``-1``: a rate of ``-100%`` makes every discount factor undefined.
DEFAULT_RATE_BOUNDS: tuple[float, float] = (-0.9999, 1.0e6)

#: Number of samples in the log-spaced bracket scan.
DEFAULT_GRID_POINTS: int = 512

#: Accrual conventions for a preferred return.
PREFERRED_COMPOUNDING: tuple[str, ...] = ("compound", "simple")

#: Preferred-return accrual assumed when the caller does not name one.
DEFAULT_PREFERRED_COMPOUNDING: str = "compound"

#: Waterfall tier labels, in the order they are paid.
TIER_RETURN_OF_CAPITAL: str = "return_of_capital"
TIER_PREFERRED_RETURN: str = "preferred_return"
TIER_CATCH_UP: str = "gp_catch_up"
TIER_CARRY_SPLIT: str = "carry_split"

#: Relative/absolute slack allowed when asserting that a waterfall's tiers sum
#: to the distributable amount. Anything looser would hide a real leak.
_ALLOCATION_TOLERANCE: float = 1e-9

#: NPV residual accepted as "this is a root", scaled by the gross flow size.
_DEFAULT_RESIDUAL_TOLERANCE: float = 1e-10

#: Two roots closer than this in rate space are the same root.
_ROOT_DEDUPE_TOLERANCE: float = 1e-9


class XIRRError(ValueError):
    """Base class for every reason an irregular-date IRR has no usable answer."""


class NoSignChangeError(XIRRError):
    """Raised when the flows never change sign, so no finite IRR exists.

    An all-negative or all-positive schedule has no rate at which its present
    value is zero (other than the degenerate limits). Returning a number here
    would be a fabricated result, so this is an error rather than a NaN.
    """


class XIRRConvergenceError(XIRRError):
    """Raised when the flows do change sign but no root could be verified.

    Both the bracketed scan and the Newton fallback failed to produce a rate
    whose net present value is zero to tolerance.
    """


# ---------------------------------------------------------------------------
# Series plumbing
# ---------------------------------------------------------------------------


def _require_series(series: CashFlowSeries, *, name: str = "series") -> CashFlowSeries:
    """Reject anything that is not a ``CashFlowSeries``.

    Args:
        series: Candidate value supplied by the caller.
        name: Parameter name to quote in the error message.

    Returns:
        The same series, unchanged.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``. A bare list of
            floats is refused on purpose -- it carries no dates, no currency and
            no kinds, and every one of those is load-bearing here.
    """
    if not isinstance(series, CashFlowSeries):
        raise TypeError(
            f"{name} must be a CashFlowSeries, got {type(series).__name__}; a bare "
            "sequence of amounts loses the dates, the currency and the flow kinds"
        )
    return series


def _select_flows(
    series: CashFlowSeries, valuations: str
) -> tuple[CashFlow, ...]:
    """Apply a valuation policy to a series' flows.

    Args:
        series: Source series, already date-ordered by its own constructor.
        valuations: One of :data:`VALUATION_POLICIES`.

    Returns:
        Date-ordered flows after the policy has been applied.

    Raises:
        ValueError: If ``valuations`` is not a recognised policy.
    """
    if valuations not in VALUATION_POLICIES:
        raise ValueError(
            f"valuations={valuations!r} is not one of {VALUATION_POLICIES}"
        )
    flows = tuple(series)
    if valuations == "all":
        return flows
    cash = [flow for flow in flows if not flow.is_valuation]
    if valuations == "exclude":
        return tuple(cash)
    marks = [flow for flow in flows if flow.is_valuation]
    if not marks:
        return tuple(cash)
    # ``flows`` is date-ordered, so the last mark is the terminal one.
    return tuple(sorted([*cash, marks[-1]], key=lambda flow: flow.date))


def _times_and_amounts(
    flows: Sequence[CashFlow], days_per_year: float
) -> tuple[np.ndarray, np.ndarray]:
    """Convert flows into year offsets from the first date, plus amounts.

    Args:
        flows: Date-ordered flows.
        days_per_year: Day basis, e.g. :data:`DEFAULT_DAYS_PER_YEAR`.

    Returns:
        Tuple ``(times, amounts)`` of float arrays; ``times[0]`` is ``0.0``.

    Raises:
        ValueError: If fewer than two flows are present, if every date is the
            same (no time span, so no rate is identifiable), or if
            ``days_per_year`` is not positive.
    """
    if days_per_year <= 0:
        raise ValueError(f"days_per_year must be positive, got {days_per_year!r}")
    if len(flows) < 2:
        raise ValueError(
            f"an irregular-date IRR needs at least two flows, got {len(flows)}"
        )
    dates = [flow.date for flow in flows]
    start = min(dates)
    if max(dates) == start:
        raise ValueError(
            f"all {len(flows)} flows fall on {start}; a rate of return is not "
            "identifiable without a time span"
        )
    times = np.array([(day - start).days / days_per_year for day in dates], dtype=float)
    amounts = np.array([flow.amount for flow in flows], dtype=float)
    return times, amounts


def _npv(rate: float, times: np.ndarray, amounts: np.ndarray) -> float:
    """Net present value of dated amounts at one rate.

    Discounting is done in log space (``exp(-t * log1p(r))``) so a large rate
    over a long horizon underflows to zero instead of overflowing a power.

    Args:
        rate: Annual rate as a decimal, strictly greater than ``-1``.
        times: Year offsets from the first flow.
        amounts: Signed amounts aligned with ``times``.

    Returns:
        The net present value, or a non-finite value when the rate is so close
        to ``-1`` that the discount factors overflow. Callers must check.
    """
    if rate <= -1.0:
        return math.nan
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        factors = np.exp(-times * np.log1p(rate))
        total = float(np.sum(amounts * factors))
    return total


def _npv_derivative(rate: float, times: np.ndarray, amounts: np.ndarray) -> float:
    """First derivative of :func:`_npv` with respect to the rate.

    Args:
        rate: Annual rate as a decimal, strictly greater than ``-1``.
        times: Year offsets from the first flow.
        amounts: Signed amounts aligned with ``times``.

    Returns:
        ``d(NPV)/d(rate)``, or a non-finite value on overflow.
    """
    if rate <= -1.0:
        return math.nan
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        factors = np.exp(-(times + 1.0) * np.log1p(rate))
        total = float(np.sum(-times * amounts * factors))
    return total


def npv(
    series: CashFlowSeries,
    rate: float,
    *,
    valuations: str = DEFAULT_VALUATION_POLICY,
    days_per_year: float = DEFAULT_DAYS_PER_YEAR,
) -> float:
    """Net present value of a dated cash-flow series at a given annual rate.

    Discounting is from the series' earliest date, so ``npv(series, 0.0)`` is
    just the sum of the selected amounts.

    Args:
        series: The cash flows. Must be a ``CashFlowSeries``.
        rate: Annual rate as a decimal (``0.08`` is 8%), strictly above ``-1``.
        valuations: One of :data:`VALUATION_POLICIES`; see that constant for
            what each policy does with NAV marks.
        days_per_year: Day basis for the year fraction.

    Returns:
        Present value at the series' first date, in the series' currency.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        ValueError: If the rate is at or below ``-1``, if the policy is
            unknown, if fewer than two flows survive the policy, or if all
            surviving flows share one date.
    """
    _require_series(series)
    if rate <= -1.0:
        raise ValueError(
            f"rate must be greater than -1 (a -100% rate makes every discount "
            f"factor undefined), got {rate!r}"
        )
    flows = _select_flows(series, valuations)
    times, amounts = _times_and_amounts(flows, days_per_year)
    value = _npv(rate, times, amounts)
    if not math.isfinite(value):
        raise ValueError(
            f"net present value overflowed at rate={rate!r}; the rate is too "
            "close to -1 for this horizon"
        )
    return value


# ---------------------------------------------------------------------------
# XIRR
# ---------------------------------------------------------------------------


def _scan_roots(
    times: np.ndarray,
    amounts: np.ndarray,
    rate_bounds: tuple[float, float],
    grid_points: int,
    residual_tolerance: float,
) -> list[float]:
    """Find every rate in a window where the NPV crosses or touches zero.

    The window is sampled on a grid that is uniform in ``log(1 + r)``, so the
    same number of points resolves rates near ``-100%`` and rates in the
    thousands of percent. Each sign change is then closed with Brent's method,
    which cannot leave its bracket and therefore cannot report a root that is
    not there.

    Args:
        times: Year offsets from the first flow.
        amounts: Signed amounts aligned with ``times``.
        rate_bounds: ``(low, high)`` rate window; ``low`` must exceed ``-1``.
        grid_points: Number of samples across the window.
        residual_tolerance: Absolute NPV magnitude counted as zero.

    Returns:
        Ascending, de-duplicated roots. Empty when the grid found no crossing.

    Raises:
        ValueError: If the bounds are not ``-1 < low < high``, or if
            ``grid_points`` is below 3.
    """
    low, high = rate_bounds
    if low <= -1.0:
        raise ValueError(f"rate_bounds lower bound must exceed -1, got {low!r}")
    if high <= low:
        raise ValueError(f"rate_bounds must be increasing, got {rate_bounds!r}")
    if grid_points < 3:
        raise ValueError(f"grid_points must be at least 3, got {grid_points!r}")

    rates = np.expm1(np.linspace(math.log1p(low), math.log1p(high), grid_points))
    values = np.array([_npv(float(rate), times, amounts) for rate in rates])

    def objective(rate: float) -> float:
        return _npv(rate, times, amounts)

    roots: list[float] = []
    previous: int | None = None
    for index, value in enumerate(values):
        if not math.isfinite(value):
            continue
        if abs(value) <= residual_tolerance:
            roots.append(float(rates[index]))
            previous = index
            continue
        if (
            previous is not None
            and abs(values[previous]) > residual_tolerance
            and (values[previous] > 0) != (value > 0)
        ):
            roots.append(
                float(brentq(objective, float(rates[previous]), float(rates[index])))
            )
        previous = index

    unique: list[float] = []
    for root in sorted(roots):
        if not unique or abs(root - unique[-1]) > _ROOT_DEDUPE_TOLERANCE:
            unique.append(root)
    return unique


def xirr_all(
    series: CashFlowSeries,
    *,
    valuations: str = DEFAULT_VALUATION_POLICY,
    days_per_year: float = DEFAULT_DAYS_PER_YEAR,
    rate_bounds: tuple[float, float] = DEFAULT_RATE_BOUNDS,
    grid_points: int = DEFAULT_GRID_POINTS,
    residual_tolerance: float = _DEFAULT_RESIDUAL_TOLERANCE,
) -> tuple[float, ...]:
    """Every irregular-date IRR of a series inside the scanned rate window.

    A schedule whose signs change more than once (recallable distributions, a
    rescue financing) can have several mathematically valid IRRs. Reporting one
    of them as *the* IRR is a judgement call, so this function hands back all of
    them and :func:`xirr` documents which one it picks.

    Args:
        series: The cash flows.
        valuations: One of :data:`VALUATION_POLICIES`.
        days_per_year: Day basis for the year fraction.
        rate_bounds: ``(low, high)`` rate window to scan; ``low`` must exceed
            ``-1``.
        grid_points: Samples across the window; raise it if a pair of roots sit
            unusually close together.
        residual_tolerance: Relative NPV magnitude counted as zero, scaled by
            the sum of absolute flow amounts.

    Returns:
        Ascending roots. Empty only when the sign check passed but the scan
        found nothing, which :func:`xirr` turns into an error.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        NoSignChangeError: If the surviving amounts are all non-negative or all
            non-positive, or are all zero.
        ValueError: For an unknown policy, a bad rate window, or fewer than two
            dated flows.
    """
    _require_series(series)
    flows = _select_flows(series, valuations)
    times, amounts = _times_and_amounts(flows, days_per_year)

    gross = float(np.sum(np.abs(amounts)))
    if gross == 0.0:
        raise NoSignChangeError(
            "every amount is zero, so no rate of return is defined"
        )
    if not (amounts > 0).any() or not (amounts < 0).any():
        direction = "positive" if (amounts > 0).any() else "negative"
        raise NoSignChangeError(
            f"all {len(amounts)} amounts are {direction}, so the net present "
            "value never crosses zero and no IRR exists. Check the sign "
            "convention: contributions must be negative and distributions "
            "positive. If a terminal NAV is missing, add it as a valuation flow."
        )

    return tuple(
        _scan_roots(
            times,
            amounts,
            rate_bounds,
            grid_points,
            residual_tolerance * gross,
        )
    )


def xirr(
    series: CashFlowSeries,
    *,
    valuations: str = DEFAULT_VALUATION_POLICY,
    days_per_year: float = DEFAULT_DAYS_PER_YEAR,
    guess: float = 0.1,
    rate_bounds: tuple[float, float] = DEFAULT_RATE_BOUNDS,
    grid_points: int = DEFAULT_GRID_POINTS,
    residual_tolerance: float = _DEFAULT_RESIDUAL_TOLERANCE,
) -> float:
    """Money-weighted return of an irregularly dated cash-flow series.

    Solves ``sum(amount_i / (1 + r) ** t_i) == 0`` where ``t_i`` is the year
    offset of flow ``i`` from the earliest flow.

    Method, and its fallback. The primary solver is a bracketed one: the rate
    window is sampled on a log-spaced grid, and each sign change is closed with
    Brent's method, which is confined to its bracket and so cannot invent a
    root. Only if that scan finds nothing -- which can happen when two roots sit
    inside one grid cell -- does a Newton iteration from ``guess`` run as a
    fallback, and its answer is accepted only after the net present value there
    is checked against ``residual_tolerance``. An unverifiable answer raises
    :class:`XIRRConvergenceError` rather than being returned.

    When several roots exist the smallest is returned, because the alternatives
    are usually artefacts of a sign pattern rather than economics. Call
    :func:`xirr_all` to see them all before trusting the number.

    Args:
        series: The cash flows. Terminal NAV should be present as a valuation
            flow; under the default policy it is included and intermediate
            marks are dropped.
        valuations: One of :data:`VALUATION_POLICIES`.
        days_per_year: Day basis for the year fraction; 365 matches Excel's
            ``XIRR``.
        guess: Starting rate for the Newton fallback only. It has no effect on
            the bracketed path, so a bad guess cannot move the answer.
        rate_bounds: ``(low, high)`` rate window to scan.
        grid_points: Samples across the window.
        residual_tolerance: Relative NPV magnitude counted as zero, scaled by
            the sum of absolute flow amounts.

    Returns:
        The annual rate as a decimal (``0.184`` is 18.4%).

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        NoSignChangeError: If the flows never change sign, so no IRR exists.
        XIRRConvergenceError: If the flows do change sign but no root could be
            found and verified inside ``rate_bounds``.
        ValueError: For an unknown policy, a bad rate window, fewer than two
            dated flows, or flows that all share one date.
    """
    roots = xirr_all(
        series,
        valuations=valuations,
        days_per_year=days_per_year,
        rate_bounds=rate_bounds,
        grid_points=grid_points,
        residual_tolerance=residual_tolerance,
    )
    if roots:
        return roots[0]

    # Documented fallback: the scan is finite, so two roots inside one grid cell
    # leave it empty. Newton can still find one -- but only a verified one is
    # allowed out.
    flows = _select_flows(series, valuations)
    times, amounts = _times_and_amounts(flows, days_per_year)
    gross = float(np.sum(np.abs(amounts)))
    tolerance = residual_tolerance * gross
    low, high = rate_bounds
    try:
        candidate = float(
            newton(
                lambda rate: _npv(rate, times, amounts),
                guess,
                fprime=lambda rate: _npv_derivative(rate, times, amounts),
            )
        )
    except (RuntimeError, OverflowError, ValueError, FloatingPointError) as exc:
        raise XIRRConvergenceError(
            f"no IRR found in ({low}, {high}] by bracketed scan, and the Newton "
            f"fallback from guess={guess!r} failed: {exc}"
        ) from exc

    residual = _npv(candidate, times, amounts)
    if (
        not math.isfinite(candidate)
        or not math.isfinite(residual)
        or candidate <= low
        or candidate > high
        or abs(residual) > tolerance
    ):
        raise XIRRConvergenceError(
            f"no IRR found in ({low}, {high}] by bracketed scan; the Newton "
            f"fallback returned {candidate!r} with a net present value of "
            f"{residual!r}, which is not a root to tolerance {tolerance!r}"
        )
    return candidate


# ---------------------------------------------------------------------------
# Fund multiples
# ---------------------------------------------------------------------------


def paid_in_capital(
    series: CashFlowSeries,
    *,
    contribution_kinds: Iterable[str] = CONTRIBUTION_KINDS,
) -> float:
    """Total capital drawn into the fund, as a positive magnitude.

    Args:
        series: The cash flows.
        contribution_kinds: Kind labels counted as capital in. Defaults to
            :data:`CONTRIBUTION_KINDS`; add ``"fee"`` if the fund's management
            fees are charged outside the commitment and you want them in the
            denominator of the multiples.

    Returns:
        Positive paid-in capital; ``0.0`` when no contributions are present.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        ValueError: If the matched flows net to an inflow, which means their
            signs contradict the holder-perspective convention.
    """
    _require_series(series)
    total = -series.filter(kind=contribution_kinds).total(include_valuations=True)
    if total < 0.0:
        raise ValueError(
            f"contributions net to an inflow of {-total!r}; under the "
            "holder-perspective sign convention capital going into a fund is "
            "negative. Check the contribution_kinds you passed."
        )
    return total


def distributed_capital(
    series: CashFlowSeries,
    *,
    distribution_kinds: Iterable[str] = DISTRIBUTION_KINDS,
) -> float:
    """Total capital returned by the fund, as a positive magnitude.

    Args:
        series: The cash flows.
        distribution_kinds: Kind labels counted as capital out. Defaults to
            :data:`DISTRIBUTION_KINDS`.

    Returns:
        Positive distributed capital; ``0.0`` when no distributions are present.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        ValueError: If the matched flows net to an outflow.
    """
    _require_series(series)
    total = series.filter(kind=distribution_kinds).total(include_valuations=True)
    if total < 0.0:
        raise ValueError(
            f"distributions net to an outflow of {-total!r}; under the "
            "holder-perspective sign convention capital coming out of a fund is "
            "positive. Check the distribution_kinds you passed."
        )
    return total


def residual_value(series: CashFlowSeries) -> float:
    """Unrealised NAV of the fund at its most recent mark.

    Only the latest valuation record counts. Earlier marks are stale by
    definition, and summing them would report a fund's NAV several times over.

    Args:
        series: The cash flows, including any valuation records (``nav``,
            ``residual_value``, ``valuation``).

    Returns:
        The latest mark's amount, or ``0.0`` when the series holds no mark --
        which is the right answer for a fully liquidated fund.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        ValueError: If the latest mark is negative. A negative NAV is possible
            in a levered vehicle but is never what a mis-signed file means, so
            it must be stated as a custom kind rather than inferred here.
    """
    _require_series(series)
    marks = [flow for flow in series if flow.is_valuation]
    if not marks:
        return 0.0
    value = marks[-1].amount
    if value < 0.0:
        raise ValueError(
            f"latest valuation on {marks[-1].date} is {value!r}; a residual "
            "value must be non-negative. If the vehicle really carries negative "
            "equity, record it under a custom kind and compute the multiple "
            "explicitly."
        )
    return value


def _require_paid_in(paid_in: float) -> float:
    """Reject a zero or negative denominator for a capital multiple.

    Args:
        paid_in: Paid-in capital as a positive magnitude.

    Returns:
        The same value.

    Raises:
        ValueError: If it is not strictly positive -- a multiple of nothing is
            undefined, and returning ``inf`` or ``0`` would both be wrong.
    """
    if paid_in <= 0.0:
        raise ValueError(
            f"paid-in capital is {paid_in!r}; a capital multiple is undefined "
            "with no capital drawn. Confirm the contribution kinds match the file."
        )
    return paid_in


def dpi(
    series: CashFlowSeries,
    *,
    contribution_kinds: Iterable[str] = CONTRIBUTION_KINDS,
    distribution_kinds: Iterable[str] = DISTRIBUTION_KINDS,
) -> float:
    """Distributions to paid-in: realised cash returned per unit drawn.

    Args:
        series: The cash flows.
        contribution_kinds: Kind labels counted as capital in.
        distribution_kinds: Kind labels counted as capital out.

    Returns:
        ``distributed / paid_in``. ``1.0`` is the point where the fund has
        returned the cash it drew.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        ValueError: If no capital was drawn, or if a sign convention is violated.
    """
    paid_in = _require_paid_in(
        paid_in_capital(series, contribution_kinds=contribution_kinds)
    )
    return (
        distributed_capital(series, distribution_kinds=distribution_kinds) / paid_in
    )


def rvpi(
    series: CashFlowSeries,
    *,
    contribution_kinds: Iterable[str] = CONTRIBUTION_KINDS,
) -> float:
    """Residual value to paid-in: unrealised NAV per unit drawn.

    Args:
        series: The cash flows, including the latest valuation mark.
        contribution_kinds: Kind labels counted as capital in.

    Returns:
        ``residual_value / paid_in``; ``0.0`` for a fully liquidated fund.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        ValueError: If no capital was drawn, or the latest mark is negative.
    """
    paid_in = _require_paid_in(
        paid_in_capital(series, contribution_kinds=contribution_kinds)
    )
    return residual_value(series) / paid_in


def tvpi(
    series: CashFlowSeries,
    *,
    contribution_kinds: Iterable[str] = CONTRIBUTION_KINDS,
    distribution_kinds: Iterable[str] = DISTRIBUTION_KINDS,
) -> float:
    """Total value to paid-in: realised plus unrealised value per unit drawn.

    By construction this is ``dpi + rvpi``; the identity is asserted in the
    tests so the three cannot drift apart.

    Args:
        series: The cash flows.
        contribution_kinds: Kind labels counted as capital in.
        distribution_kinds: Kind labels counted as capital out.

    Returns:
        ``(distributed + residual_value) / paid_in``.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        ValueError: If no capital was drawn, or a sign convention is violated.
    """
    paid_in = _require_paid_in(
        paid_in_capital(series, contribution_kinds=contribution_kinds)
    )
    distributed = distributed_capital(
        series, distribution_kinds=distribution_kinds
    )
    return (distributed + residual_value(series)) / paid_in


def moic(
    series: CashFlowSeries,
    *,
    invested_capital: float | None = None,
    contribution_kinds: Iterable[str] = CONTRIBUTION_KINDS,
    distribution_kinds: Iterable[str] = DISTRIBUTION_KINDS,
) -> float:
    """Multiple on invested capital: total value over the capital put to work.

    At fund level with paid-in as the denominator this equals :func:`tvpi`. The
    two diverge only when the denominator differs -- a deal-level MOIC usually
    excludes fees and undeployed capital -- so that denominator is an explicit
    argument rather than a hidden convention.

    Args:
        series: The cash flows.
        invested_capital: Denominator to use, as a positive magnitude. ``None``
            uses paid-in capital, making the result identical to TVPI.
        contribution_kinds: Kind labels counted as capital in, used only when
            ``invested_capital`` is ``None``.
        distribution_kinds: Kind labels counted as capital out.

    Returns:
        ``(distributed + residual_value) / invested_capital``.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        ValueError: If the denominator is not strictly positive, or a sign
            convention is violated.
    """
    if invested_capital is None:
        denominator = paid_in_capital(series, contribution_kinds=contribution_kinds)
    else:
        denominator = float(invested_capital)
    _require_paid_in(denominator)
    distributed = distributed_capital(
        series, distribution_kinds=distribution_kinds
    )
    return (distributed + residual_value(series)) / denominator


@dataclass(frozen=True)
class FundMultiples:
    """The standard LP reporting multiples, computed from one series.

    Attributes:
        paid_in: Capital drawn, positive magnitude.
        distributed: Capital returned, positive magnitude.
        residual_value: Latest NAV mark, ``0.0`` when fully liquidated.
        dpi: ``distributed / paid_in``.
        rvpi: ``residual_value / paid_in``.
        tvpi: ``dpi + rvpi``.
        moic: Total value over the invested-capital denominator, equal to
            ``tvpi`` when that denominator is paid-in capital.
        invested_capital: The denominator actually used for ``moic``.
    """

    paid_in: float
    distributed: float
    residual_value: float
    dpi: float
    rvpi: float
    tvpi: float
    moic: float
    invested_capital: float


def fund_multiples(
    series: CashFlowSeries,
    *,
    invested_capital: float | None = None,
    contribution_kinds: Iterable[str] = CONTRIBUTION_KINDS,
    distribution_kinds: Iterable[str] = DISTRIBUTION_KINDS,
) -> FundMultiples:
    """Compute DPI, RVPI, TVPI and MOIC in one pass over a series.

    Args:
        series: The cash flows.
        invested_capital: Denominator for MOIC; ``None`` uses paid-in capital.
        contribution_kinds: Kind labels counted as capital in.
        distribution_kinds: Kind labels counted as capital out.

    Returns:
        A :class:`FundMultiples` whose ``tvpi`` is exactly ``dpi + rvpi``.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        ValueError: If no capital was drawn, if the MOIC denominator is not
            positive, or if a sign convention is violated.
    """
    paid_in = _require_paid_in(
        paid_in_capital(series, contribution_kinds=contribution_kinds)
    )
    distributed = distributed_capital(
        series, distribution_kinds=distribution_kinds
    )
    residual = residual_value(series)
    denominator = paid_in if invested_capital is None else float(invested_capital)
    _require_paid_in(denominator)

    distributed_to_paid_in = distributed / paid_in
    residual_to_paid_in = residual / paid_in
    return FundMultiples(
        paid_in=paid_in,
        distributed=distributed,
        residual_value=residual,
        dpi=distributed_to_paid_in,
        rvpi=residual_to_paid_in,
        # Written as the sum, not recomputed, so the identity holds bit-for-bit.
        tvpi=distributed_to_paid_in + residual_to_paid_in,
        moic=(distributed + residual) / denominator,
        invested_capital=denominator,
    )


# ---------------------------------------------------------------------------
# European (whole-of-fund) waterfall
# ---------------------------------------------------------------------------


def preferred_return_amount(
    series: CashFlowSeries,
    *,
    rate: float,
    as_of: _dt.date | _dt.datetime | str | None = None,
    compounding: str = DEFAULT_PREFERRED_COMPOUNDING,
    days_per_year: float = DEFAULT_DAYS_PER_YEAR,
    contribution_kinds: Iterable[str] = CONTRIBUTION_KINDS,
) -> float:
    """Preferred return owed to LPs, accrued per contribution from its own date.

    Accruing each draw separately matters: a fund that called half its capital
    three years after the first close owes materially less hurdle than one that
    called everything on day one, and a single fund-level ``years`` figure
    cannot express that.

    Args:
        series: The cash flows.
        rate: Annual preferred rate as a decimal (``0.08`` is an 8% hurdle).
            Must be non-negative.
        as_of: Measurement date. ``None`` uses the series' latest flow date.
        compounding: One of :data:`PREFERRED_COMPOUNDING`. ``"compound"``
            accrues ``(1 + rate) ** years - 1``; ``"simple"`` accrues
            ``rate * years``.
        days_per_year: Day basis for the year fraction.
        contribution_kinds: Kind labels counted as capital in.

    Returns:
        The preferred return owed, as a positive magnitude excluding the return
        of capital itself. ``0.0`` when nothing has been drawn.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        ValueError: If the rate is negative, the compounding convention is
            unknown, ``as_of`` precedes a contribution, or ``as_of`` is omitted
            for an empty series.
    """
    _require_series(series)
    if rate < 0.0:
        raise ValueError(f"preferred rate must be non-negative, got {rate!r}")
    if compounding not in PREFERRED_COMPOUNDING:
        raise ValueError(
            f"compounding={compounding!r} is not one of {PREFERRED_COMPOUNDING}"
        )
    if days_per_year <= 0:
        raise ValueError(f"days_per_year must be positive, got {days_per_year!r}")

    contributions = tuple(series.filter(kind=contribution_kinds))
    if not contributions:
        return 0.0

    if as_of is None:
        if series.is_empty:
            raise ValueError("as_of is required for an empty series")
        measurement = max(series.dates())
    else:
        measurement = normalize_date(as_of, field_name="as_of")

    total = 0.0
    for flow in contributions:
        years = (measurement - flow.date).days / days_per_year
        if years < 0.0:
            raise ValueError(
                f"contribution on {flow.date} is after as_of={measurement}; a "
                "preferred return cannot accrue backwards"
            )
        principal = -flow.amount
        if compounding == "compound":
            total += principal * ((1.0 + rate) ** years - 1.0)
        else:
            total += principal * rate * years
    return total


@dataclass(frozen=True)
class WaterfallTier:
    """One tier of a distribution waterfall and how it split.

    Attributes:
        name: Tier label, one of the ``TIER_*`` constants.
        amount: Dollars that flowed through this tier.
        lp_amount: Portion allocated to the limited partners.
        gp_amount: Portion allocated to the general partner.
    """

    name: str
    amount: float
    lp_amount: float
    gp_amount: float

    def __post_init__(self) -> None:
        """Check that the tier's two shares account for the whole tier.

        Raises:
            ValueError: If ``lp_amount + gp_amount`` differs from ``amount``, or
                if any figure is negative. A waterfall that does not conserve
                cash is a bug, never a rounding artefact worth tolerating.
        """
        for label, value in (
            ("amount", self.amount),
            ("lp_amount", self.lp_amount),
            ("gp_amount", self.gp_amount),
        ):
            if value < -_ALLOCATION_TOLERANCE:
                raise ValueError(
                    f"tier {self.name!r} has a negative {label} of {value!r}"
                )
        if not math.isclose(
            self.lp_amount + self.gp_amount,
            self.amount,
            rel_tol=_ALLOCATION_TOLERANCE,
            abs_tol=_ALLOCATION_TOLERANCE,
        ):
            raise ValueError(
                f"tier {self.name!r} does not conserve cash: lp {self.lp_amount!r} "
                f"+ gp {self.gp_amount!r} != {self.amount!r}"
            )


@dataclass(frozen=True)
class WaterfallResult:
    """Outcome of a European whole-of-fund distribution waterfall.

    Attributes:
        distributable: Cash entering the waterfall.
        contributed_capital: LP capital that must be returned first.
        preferred_amount: Preferred return owed before the GP earns anything.
        tiers: The tiers in payment order.
        lp_total: Everything allocated to the limited partners.
        gp_total: Everything allocated to the general partner.
        unreturned_capital: Contributed capital still outstanding, positive when
            the distributable amount did not cover the return of capital.
        unpaid_preferred: Preferred return still owed, positive when the
            distributable amount did not clear the hurdle.
    """

    distributable: float
    contributed_capital: float
    preferred_amount: float
    tiers: tuple[WaterfallTier, ...]
    lp_total: float
    gp_total: float
    unreturned_capital: float
    unpaid_preferred: float

    def __post_init__(self) -> None:
        """Check that the tiers reconcile to the distributable amount.

        Raises:
            ValueError: If the tier amounts do not sum to ``distributable``, or
                if the per-side totals do not match the tier splits.
        """
        checks = (
            ("distributable", sum(tier.amount for tier in self.tiers), self.distributable),
            ("lp_total", sum(tier.lp_amount for tier in self.tiers), self.lp_total),
            ("gp_total", sum(tier.gp_amount for tier in self.tiers), self.gp_total),
            ("lp+gp", self.lp_total + self.gp_total, self.distributable),
        )
        for label, computed, expected in checks:
            if not math.isclose(
                computed,
                expected,
                rel_tol=_ALLOCATION_TOLERANCE,
                abs_tol=_ALLOCATION_TOLERANCE,
            ):
                raise ValueError(
                    f"waterfall does not reconcile on {label}: tiers give "
                    f"{computed!r}, expected {expected!r}"
                )

    def tier_amount(self, name: str) -> float:
        """Dollars that flowed through a named tier.

        Args:
            name: One of the ``TIER_*`` constants.

        Returns:
            The tier's ``amount``, or ``0.0`` if that tier is absent.
        """
        for tier in self.tiers:
            if tier.name == name:
                return tier.amount
        return 0.0

    @property
    def gp_profit_share(self) -> float:
        """Fraction of distributed profit that the GP received.

        Profit is everything distributed beyond the return of capital, so this
        is the realised carry percentage. It reaches the nominal carry rate only
        once the catch-up has completed.

        Returns:
            GP total over distributed profit, or ``0.0`` when no profit was
            distributed.
        """
        profit = self.distributable - self.tier_amount(TIER_RETURN_OF_CAPITAL)
        if profit <= 0.0:
            return 0.0
        return self.gp_total / profit


def waterfall_split(
    distributable: float,
    contributed_capital: float,
    preferred_amount: float,
    *,
    carry_rate: float,
    catch_up_rate: float = 1.0,
) -> WaterfallResult:
    """Split a distributable amount through the four European waterfall tiers.

    The tiers, paid strictly in order and only from what remains:

    1. **Return of capital** -- 100% to LPs until ``contributed_capital`` is back.
    2. **Preferred return** -- 100% to LPs until ``preferred_amount`` is paid.
    3. **GP catch-up** -- ``catch_up_rate`` to the GP, the rest to LPs, until the
       GP holds ``carry_rate`` of all profit distributed so far. Solving
       ``catch_up_rate * C == carry_rate * (preferred_paid + C)`` gives the tier
       size ``C = carry_rate * preferred_paid / (catch_up_rate - carry_rate)``.
       With a 100% catch-up and 20% carry that is ``0.25 * preferred_paid``, and
       the GP then holds exactly 20% of the ``1.25 * preferred_paid`` of profit
       distributed.
    4. **Carry split** -- the remainder splits ``carry_rate`` to the GP.

    Every rate is a parameter; nothing about 8% / 20% / 100% is baked in.

    Args:
        distributable: Cash to distribute, non-negative.
        contributed_capital: LP capital to return first, non-negative.
        preferred_amount: Preferred return owed, non-negative. See
            :func:`preferred_return_amount` to derive it from a series.
        carry_rate: GP share of profit above the hurdle, in ``[0, 1)``.
        catch_up_rate: GP share of each catch-up dollar, in ``[0, 1]``. Pass
            ``0.0`` for a fund with no catch-up tier; any other value must
            exceed ``carry_rate``, or the tier could never complete.

    Returns:
        A :class:`WaterfallResult` whose tiers sum exactly to ``distributable``.

    Raises:
        ValueError: If any amount is negative, if ``carry_rate`` is outside
            ``[0, 1)``, if ``catch_up_rate`` is outside ``[0, 1]``, or if a
            non-zero ``catch_up_rate`` does not exceed ``carry_rate``.
    """
    for label, value in (
        ("distributable", distributable),
        ("contributed_capital", contributed_capital),
        ("preferred_amount", preferred_amount),
    ):
        if value < 0.0:
            raise ValueError(f"{label} must be non-negative, got {value!r}")
    if not 0.0 <= carry_rate < 1.0:
        raise ValueError(f"carry_rate must be in [0, 1), got {carry_rate!r}")
    if not 0.0 <= catch_up_rate <= 1.0:
        raise ValueError(f"catch_up_rate must be in [0, 1], got {catch_up_rate!r}")
    if catch_up_rate > 0.0 and catch_up_rate <= carry_rate:
        raise ValueError(
            f"catch_up_rate={catch_up_rate!r} must exceed carry_rate="
            f"{carry_rate!r}, otherwise the catch-up tier can never complete. "
            "Pass catch_up_rate=0.0 for a fund with no catch-up."
        )

    remaining = float(distributable)

    return_of_capital = min(remaining, float(contributed_capital))
    remaining -= return_of_capital

    preferred_paid = min(remaining, float(preferred_amount))
    remaining -= preferred_paid

    if catch_up_rate > 0.0 and carry_rate > 0.0:
        catch_up_target = carry_rate * preferred_paid / (catch_up_rate - carry_rate)
    else:
        catch_up_target = 0.0
    catch_up = min(remaining, catch_up_target)
    remaining -= catch_up

    residual = remaining
    gp_residual = carry_rate * residual

    tiers = (
        WaterfallTier(
            name=TIER_RETURN_OF_CAPITAL,
            amount=return_of_capital,
            lp_amount=return_of_capital,
            gp_amount=0.0,
        ),
        WaterfallTier(
            name=TIER_PREFERRED_RETURN,
            amount=preferred_paid,
            lp_amount=preferred_paid,
            gp_amount=0.0,
        ),
        WaterfallTier(
            name=TIER_CATCH_UP,
            amount=catch_up,
            lp_amount=catch_up * (1.0 - catch_up_rate),
            gp_amount=catch_up * catch_up_rate,
        ),
        WaterfallTier(
            name=TIER_CARRY_SPLIT,
            amount=residual,
            lp_amount=residual - gp_residual,
            gp_amount=gp_residual,
        ),
    )
    return WaterfallResult(
        distributable=float(distributable),
        contributed_capital=float(contributed_capital),
        preferred_amount=float(preferred_amount),
        tiers=tiers,
        lp_total=sum(tier.lp_amount for tier in tiers),
        gp_total=sum(tier.gp_amount for tier in tiers),
        unreturned_capital=float(contributed_capital) - return_of_capital,
        unpaid_preferred=float(preferred_amount) - preferred_paid,
    )


def european_waterfall(
    series: CashFlowSeries,
    *,
    preferred_rate: float,
    carry_rate: float,
    catch_up_rate: float = 1.0,
    as_of: _dt.date | _dt.datetime | str | None = None,
    compounding: str = DEFAULT_PREFERRED_COMPOUNDING,
    days_per_year: float = DEFAULT_DAYS_PER_YEAR,
    include_residual: bool = False,
    contribution_kinds: Iterable[str] = CONTRIBUTION_KINDS,
    distribution_kinds: Iterable[str] = DISTRIBUTION_KINDS,
) -> WaterfallResult:
    """Run a European whole-of-fund waterfall over an actual cash-flow series.

    Contributed capital and the distributable amount are read off the series,
    and the preferred return is accrued per contribution by
    :func:`preferred_return_amount`. The split itself is
    :func:`waterfall_split`.

    Args:
        series: The fund's cash flows.
        preferred_rate: Annual hurdle rate as a decimal.
        carry_rate: GP share of profit above the hurdle, in ``[0, 1)``.
        catch_up_rate: GP share of each catch-up dollar; ``0.0`` for no catch-up.
        as_of: Measurement date for the preferred accrual. ``None`` uses the
            series' latest flow date.
        compounding: One of :data:`PREFERRED_COMPOUNDING`.
        days_per_year: Day basis for the preferred accrual.
        include_residual: ``False`` (default) distributes only realised cash,
            which is what a European waterfall actually does. ``True`` adds the
            latest NAV mark for an "if the fund liquidated today" view -- a
            projection, not a settled split.
        contribution_kinds: Kind labels counted as capital in.
        distribution_kinds: Kind labels counted as capital out.

    Returns:
        A :class:`WaterfallResult` whose tiers sum exactly to the distributable
        amount.

    Raises:
        TypeError: If ``series`` is not a ``CashFlowSeries``.
        ValueError: For a negative rate, an unknown compounding convention, a
            carry or catch-up rate outside its allowed range, a contribution
            dated after ``as_of``, or a sign-convention violation.
    """
    _require_series(series)
    contributed = paid_in_capital(series, contribution_kinds=contribution_kinds)
    distributable = distributed_capital(
        series, distribution_kinds=distribution_kinds
    )
    if include_residual:
        distributable += residual_value(series)
    preferred = preferred_return_amount(
        series,
        rate=preferred_rate,
        as_of=as_of,
        compounding=compounding,
        days_per_year=days_per_year,
        contribution_kinds=contribution_kinds,
    )
    return waterfall_split(
        distributable,
        contributed,
        preferred,
        carry_rate=carry_rate,
        catch_up_rate=catch_up_rate,
    )
