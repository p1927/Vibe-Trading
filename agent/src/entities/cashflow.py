"""Irregular dated cash flows: the non-bar half of the asset spine.

SIGN CONVENTION (stated once here, enforced in code, never left to callers)
--------------------------------------------------------------------------
Every amount is expressed **from the holder's point of view**:

    amount > 0  cash comes IN to the holder   (distribution, coupon, proceeds)
    amount < 0  cash goes OUT from the holder (capital call, purchase, fee)

This is the ordinary IRR/XIRR convention, so a ``CashFlowSeries`` can be fed
straight to a money-weighted return without a caller-side sign flip. Because
callers reliably get this backwards when reading a broker export, the canonical
kinds in ``KIND_DIRECTION`` are *enforced* at construction: a ``capital_call``
with a positive amount is rejected, not quietly accepted.

A movement that is genuinely two-directional (a recallable distribution, an
insurance claim whose direction depends on whether you are the insurer or the
policyholder) must be given its own ``kind``. Kinds absent from
``KIND_DIRECTION`` carry no sign constraint, so the escape hatch is to name the
flow honestly rather than to disable the check.

VALUATIONS ARE NOT CASH
-----------------------
A NAV mark is a valuation, not a settled movement. It is representable here
because terminal NAV is required to compute a fund's IRR or TVPI, but it is
tagged via ``CashFlow.is_valuation`` and excluded from ``CashFlowSeries.total()``
unless explicitly requested. Summing a NAV into realised cash would overstate
distributions, so the default is the conservative one.

CURRENCY IS REQUIRED
--------------------
Every flow carries a currency and a series refuses to mix them. The composite
backtest engine once summed five currencies into a single equity curve; that
was a shipped defect. A series of genuinely translated flows must say so
explicitly via ``pre_translated=True`` *and* name its reporting currency.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import Any

from src.entities.models import normalize_currency, normalize_date

__all__ = [
    "CashFlow",
    "CashFlowSeries",
    "CurrencyMismatchError",
    "KIND_DIRECTION",
    "VALUATION_KINDS",
    "normalize_kind",
]

#: Canonical kinds whose direction is definitional, mapped to the required sign
#: of ``amount`` (``-1`` outflow, ``+1`` inflow). A kind absent from this table
#: carries no sign constraint -- see the module docstring.
KIND_DIRECTION: Mapping[str, int] = MappingProxyType(
    {
        # Cash leaving the holder.
        "contribution": -1,
        "capital_call": -1,
        "purchase": -1,
        "subscription": -1,
        "fee": -1,
        "expense": -1,
        # Cash arriving at the holder.
        "distribution": 1,
        "dividend": 1,
        "coupon": 1,
        "interest": 1,
        "principal": 1,
        "redemption": 1,
        "proceeds": 1,
    }
)

#: Kinds that record a mark rather than a settled cash movement. Excluded from
#: ``CashFlowSeries.total()`` by default.
VALUATION_KINDS: frozenset[str] = frozenset({"nav", "residual_value", "valuation"})


class CurrencyMismatchError(ValueError):
    """Raised when a series would combine amounts in different currencies."""


def normalize_kind(value: str) -> str:
    """Normalize a cash-flow kind label to its canonical snake_case form.

    Real files spell the same concept as ``"Capital Call"``, ``"capital-call"``,
    or ``"CAPITAL_CALL"``. Normalizing means the sign check in ``KIND_DIRECTION``
    actually bites on those files instead of silently treating each spelling as
    an unconstrained custom kind.

    Args:
        value: Raw kind label from a caller or a file.

    Returns:
        Lower-cased label with spaces and hyphens collapsed to underscores.

    Raises:
        ValueError: If the label is not a string or is blank.
    """
    if not isinstance(value, str):
        raise ValueError(f"kind must be a string, got {type(value).__name__}")
    cleaned = value.strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    if not cleaned:
        raise ValueError("kind is required and cannot be empty")
    return cleaned


@dataclass(frozen=True)
class CashFlow:
    """A single dated cash amount in one currency.

    Attributes:
        date: Settlement date. ``datetime`` and ISO-8601 strings are accepted
            and normalized to ``datetime.date``.
        amount: Signed amount, positive into the holder. See the module
            docstring for the convention and its enforcement.
        kind: Canonical kind label, e.g. ``"capital_call"`` or ``"coupon"``.
            Normalized via ``normalize_kind``.
        currency: Required currency code, normalized to uppercase.
        metadata: Free-form extra fields, exposed as a read-only mapping so the
            flow stays immutable. Ingestion puts unmapped file columns here
            rather than discarding them.
    """

    date: date
    amount: float
    kind: str
    currency: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize every field and enforce the sign convention.

        Raises:
            ValueError: If the date is unsupported, the amount is not finite,
                the currency or kind is blank, or the amount's sign contradicts
                a canonical kind in ``KIND_DIRECTION``.
        """
        object.__setattr__(self, "date", normalize_date(self.date))
        object.__setattr__(self, "kind", normalize_kind(self.kind))
        object.__setattr__(self, "currency", normalize_currency(self.currency))

        try:
            amount = float(self.amount)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"amount must be numeric, got {self.amount!r}"
            ) from exc
        if not math.isfinite(amount):
            raise ValueError(
                f"amount must be a finite number, got {self.amount!r}; a missing "
                "value must be fixed at the source, not carried as NaN"
            )
        object.__setattr__(self, "amount", amount)

        required_sign = KIND_DIRECTION.get(self.kind)
        if required_sign is not None and amount != 0.0:
            if (amount > 0) != (required_sign > 0):
                direction = "positive (cash in)" if required_sign > 0 else "negative (cash out)"
                raise ValueError(
                    f"kind={self.kind!r} must have a {direction} amount under the "
                    f"holder-perspective sign convention, got {amount!r}. Flip the "
                    "sign, or use a distinct kind if this flow is genuinely "
                    "two-directional (e.g. 'recallable_distribution')."
                )

        if not isinstance(self.metadata, Mapping):
            raise ValueError(
                f"metadata must be a mapping, got {type(self.metadata).__name__}"
            )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def is_valuation(self) -> bool:
        """Whether this record is a mark rather than a settled cash movement.

        Returns:
            True when ``kind`` is one of ``VALUATION_KINDS``.
        """
        return self.kind in VALUATION_KINDS


@dataclass(frozen=True)
class CashFlowSeries:
    """An ordered, immutable collection of ``CashFlow`` in one currency.

    Flows are sorted by date at construction with a stable sort, so several
    flows on the same date keep the order they were supplied in (a call and a
    distribution on the same day usually have a meaningful sequence).

    Attributes:
        flows: The ordered flows, as a tuple.
        currency: Reporting currency of the series. Inferred from the flows
            when they agree; required explicitly when ``pre_translated`` is set.
            ``None`` only for an empty series with no declared currency.
        pre_translated: Set to True to assert that mixed-currency inputs have
            already been converted into ``currency``. This is the *only* way to
            build a series from flows with differing currency codes.
    """

    flows: tuple[CashFlow, ...] = ()
    currency: str | None = None
    pre_translated: bool = False

    def __post_init__(self) -> None:
        """Validate membership and currency coherence, then order by date.

        Raises:
            ValueError: If a member is not a ``CashFlow``, or if
                ``pre_translated`` is set without naming a reporting currency.
            CurrencyMismatchError: If the flows span multiple currencies while
                ``pre_translated`` is False, or contradict a declared currency.
        """
        flows = tuple(self.flows)
        for item in flows:
            if not isinstance(item, CashFlow):
                raise ValueError(
                    f"CashFlowSeries members must be CashFlow, got "
                    f"{type(item).__name__}"
                )

        declared = (
            normalize_currency(self.currency) if self.currency is not None else None
        )
        present = {flow.currency for flow in flows}

        if self.pre_translated:
            if declared is None:
                raise ValueError(
                    "pre_translated=True asserts the flows were already converted, "
                    "so the reporting currency must be named explicitly via "
                    "currency=..."
                )
        else:
            if len(present) > 1:
                raise CurrencyMismatchError(
                    "cash flows span multiple currencies "
                    f"({', '.join(sorted(present))}); convert them first and pass "
                    "pre_translated=True with the reporting currency, rather than "
                    "summing across currencies"
                )
            if declared is None:
                declared = next(iter(present)) if present else None
            elif present and declared not in present:
                raise CurrencyMismatchError(
                    f"declared currency {declared!r} does not match the flows' "
                    f"currency {next(iter(present))!r}"
                )

        object.__setattr__(self, "currency", declared)
        object.__setattr__(self, "flows", tuple(sorted(flows, key=lambda f: f.date)))

    # ─── Access ───

    def __iter__(self) -> Iterator[CashFlow]:
        """Iterate the flows in date order.

        Returns:
            An iterator over ``CashFlow`` sorted by date.
        """
        return iter(self.flows)

    def __len__(self) -> int:
        """Return the number of flows.

        Returns:
            Count of flows, zero for an empty series.
        """
        return len(self.flows)

    def __getitem__(self, index: int) -> CashFlow:
        """Return the flow at ``index`` in date order.

        Args:
            index: Zero-based position.

        Returns:
            The ``CashFlow`` at that position.

        Raises:
            IndexError: If the position is out of range.
        """
        return self.flows[index]

    @property
    def is_empty(self) -> bool:
        """Whether the series holds no flows.

        Returns:
            True when there are zero flows.
        """
        return not self.flows

    def dates(self) -> tuple[date, ...]:
        """Return every flow date, in order.

        Returns:
            Tuple of dates, possibly with repeats; empty for an empty series.
        """
        return tuple(flow.date for flow in self.flows)

    def amounts(self) -> tuple[float, ...]:
        """Return every signed amount, in date order.

        Returns:
            Tuple of amounts aligned with ``dates()``; empty for an empty series.
        """
        return tuple(flow.amount for flow in self.flows)

    def kinds(self) -> tuple[str, ...]:
        """Return the distinct kinds present, sorted alphabetically.

        Returns:
            Tuple of canonical kind labels; empty for an empty series.
        """
        return tuple(sorted({flow.kind for flow in self.flows}))

    # ─── Derivation ───

    def _rebuild(self, flows: Iterable[CashFlow]) -> CashFlowSeries:
        """Build a derived series that keeps this series' currency contract.

        Args:
            flows: Subset of flows for the new series.

        Returns:
            A new ``CashFlowSeries`` carrying the same currency and
            ``pre_translated`` flag, so a filtered empty series still remembers
            what currency it reports in.
        """
        return CashFlowSeries(
            flows=tuple(flows),
            currency=self.currency,
            pre_translated=self.pre_translated,
        )

    def filter(self, kind: str | Iterable[str] | None = None) -> CashFlowSeries:
        """Select flows by kind.

        Args:
            kind: A single kind label or an iterable of labels. Labels are
                normalized, so ``"Capital Call"`` matches ``"capital_call"``.
                ``None`` returns an equivalent series.

        Returns:
            A new ``CashFlowSeries`` holding only the matching flows.

        Raises:
            ValueError: If a supplied label is blank or not a string.
        """
        if kind is None:
            return self._rebuild(self.flows)
        if isinstance(kind, str):
            wanted = {normalize_kind(kind)}
        else:
            wanted = {normalize_kind(item) for item in kind}
        return self._rebuild(flow for flow in self.flows if flow.kind in wanted)

    def between(
        self,
        start: date | datetime | str | None = None,
        end: date | datetime | str | None = None,
    ) -> CashFlowSeries:
        """Select flows within an inclusive date window.

        Args:
            start: Earliest date to keep, inclusive. ``None`` leaves the window
                open on the left.
            end: Latest date to keep, inclusive. ``None`` leaves it open on the
                right.

        Returns:
            A new ``CashFlowSeries`` holding only flows inside the window.

        Raises:
            ValueError: If a bound is not a supported date value, or if
                ``start`` is later than ``end``.
        """
        lower = normalize_date(start, field_name="start") if start is not None else None
        upper = normalize_date(end, field_name="end") if end is not None else None
        if lower is not None and upper is not None and lower > upper:
            raise ValueError(f"start {lower} is after end {upper}")
        return self._rebuild(
            flow
            for flow in self.flows
            if (lower is None or flow.date >= lower)
            and (upper is None or flow.date <= upper)
        )

    def total(self, *, include_valuations: bool = False) -> float:
        """Sum the signed amounts.

        Valuation marks such as NAV are excluded by default: adding a mark to
        settled cash would overstate what was actually received.

        Args:
            include_valuations: Set True to include ``VALUATION_KINDS`` records,
                as required when computing a fund's IRR against terminal NAV.

        Returns:
            The signed total, ``0.0`` for an empty series.
        """
        return float(
            sum(
                flow.amount
                for flow in self.flows
                if include_valuations or not flow.is_valuation
            )
        )
