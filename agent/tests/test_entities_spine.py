"""Unit tests for the entity and irregular cash-flow spine."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime

import pytest

from src.entities.cashflow import (
    CashFlow,
    CashFlowSeries,
    CurrencyMismatchError,
    normalize_kind,
)
from src.entities.ingest import CashFlowIngestError, load_cashflows
from src.entities.models import (
    Bond,
    Entity,
    EntityType,
    Fund,
    FundStructure,
    Instrument,
    Security,
    SecurityType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flow(day: int, amount: float, kind: str = "coupon", currency: str = "USD") -> CashFlow:
    """Build a CashFlow on 2024-01-<day> for terse test construction."""
    return CashFlow(date=date(2024, 1, day), amount=amount, kind=kind, currency=currency)


def _write_csv(tmp_path, name: str, text: str, encoding: str = "utf-8"):
    """Write a fixture file and return its path."""
    path = tmp_path / name
    path.write_text(text, encoding=encoding)
    return path


# ---------------------------------------------------------------------------
# CashFlow construction and immutability
# ---------------------------------------------------------------------------


def test_cashflow_constructs_and_normalizes():
    flow = CashFlow(date="2024-03-05", amount=-1000, kind="Capital Call", currency=" usd ")
    assert flow.date == date(2024, 3, 5)
    assert flow.amount == -1000.0
    assert isinstance(flow.amount, float)
    assert flow.kind == "capital_call"
    assert flow.currency == "USD"


def test_cashflow_accepts_datetime_and_truncates_to_date():
    flow = CashFlow(
        date=datetime(2024, 3, 5, 16, 30), amount=10.0, kind="coupon", currency="USD"
    )
    assert flow.date == date(2024, 3, 5)
    assert not isinstance(flow.date, datetime)


def test_cashflow_is_immutable():
    flow = _flow(1, 100.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        flow.amount = 999.0


def test_cashflow_metadata_is_read_only():
    flow = CashFlow(
        date=date(2024, 1, 1),
        amount=100.0,
        kind="coupon",
        currency="USD",
        metadata={"note": "semi-annual"},
    )
    assert flow.metadata["note"] == "semi-annual"
    with pytest.raises(TypeError):
        flow.metadata["note"] = "tampered"


def test_cashflow_metadata_copies_input_so_later_mutation_cannot_leak_in():
    source = {"note": "original"}
    flow = CashFlow(
        date=date(2024, 1, 1),
        amount=100.0,
        kind="coupon",
        currency="USD",
        metadata=source,
    )
    source["note"] = "changed after construction"
    assert flow.metadata["note"] == "original"


def test_cashflow_rejects_missing_currency():
    with pytest.raises(ValueError, match="currency is required"):
        CashFlow(date=date(2024, 1, 1), amount=1.0, kind="coupon", currency="")


def test_cashflow_rejects_non_finite_amount():
    with pytest.raises(ValueError, match="finite"):
        CashFlow(date=date(2024, 1, 1), amount=float("nan"), kind="coupon", currency="USD")


def test_cashflow_rejects_ambiguous_date_string():
    with pytest.raises(ValueError, match="ISO-8601"):
        CashFlow(date="03/04/2024", amount=1.0, kind="coupon", currency="USD")


# ---------------------------------------------------------------------------
# Sign convention -- enforced centrally, not by each caller
# ---------------------------------------------------------------------------


def test_sign_convention_rejects_positive_capital_call():
    with pytest.raises(ValueError, match="negative"):
        CashFlow(date=date(2024, 1, 1), amount=1000.0, kind="capital_call", currency="USD")


def test_sign_convention_rejects_negative_distribution():
    with pytest.raises(ValueError, match="positive"):
        CashFlow(date=date(2024, 1, 1), amount=-500.0, kind="distribution", currency="USD")


def test_sign_convention_applies_after_kind_normalization():
    """'Capital Call' must be checked as capital_call, not treated as custom."""
    with pytest.raises(ValueError, match="negative"):
        CashFlow(date=date(2024, 1, 1), amount=1000.0, kind="Capital Call", currency="USD")


def test_custom_kind_carries_no_sign_constraint():
    """The documented escape hatch: name the flow, do not disable the check."""
    clawback = CashFlow(
        date=date(2024, 1, 1),
        amount=-250.0,
        kind="recallable_distribution",
        currency="USD",
    )
    assert clawback.amount == -250.0


def test_zero_amount_is_allowed_for_a_signed_kind():
    waived = CashFlow(date=date(2024, 1, 1), amount=0.0, kind="fee", currency="USD")
    assert waived.amount == 0.0


def test_normalize_kind_collapses_separators():
    assert normalize_kind("  Capital -- Call ") == "capital_call"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_series_orders_by_date():
    series = CashFlowSeries(flows=(_flow(10, 1.0), _flow(2, 2.0), _flow(7, 3.0)))
    assert series.dates() == (date(2024, 1, 2), date(2024, 1, 7), date(2024, 1, 10))
    assert series.amounts() == (2.0, 3.0, 1.0)


def test_series_ordering_is_stable_within_a_date():
    first = _flow(5, -100.0, kind="capital_call")
    second = _flow(5, 400.0, kind="distribution")
    series = CashFlowSeries(flows=(first, second))
    assert list(series) == [first, second]


def test_series_is_immutable():
    series = CashFlowSeries(flows=(_flow(1, 1.0),))
    with pytest.raises(dataclasses.FrozenInstanceError):
        series.flows = ()
    assert isinstance(series.flows, tuple)


# ---------------------------------------------------------------------------
# Currency coherence
# ---------------------------------------------------------------------------


def test_series_rejects_mixed_currencies():
    with pytest.raises(CurrencyMismatchError, match="multiple currencies"):
        CashFlowSeries(flows=(_flow(1, 1.0, currency="USD"), _flow(2, 1.0, currency="EUR")))


def test_series_infers_currency_from_its_flows():
    series = CashFlowSeries(flows=(_flow(1, 1.0, currency="jpy"),))
    assert series.currency == "JPY"


def test_series_rejects_declared_currency_contradicting_the_flows():
    with pytest.raises(CurrencyMismatchError, match="does not match"):
        CashFlowSeries(flows=(_flow(1, 1.0, currency="USD"),), currency="EUR")


def test_pre_translated_allows_mixed_currencies_when_told_explicitly():
    series = CashFlowSeries(
        flows=(_flow(1, 1.0, currency="USD"), _flow(2, 2.0, currency="EUR")),
        currency="USD",
        pre_translated=True,
    )
    assert series.currency == "USD"
    assert series.total() == pytest.approx(3.0)


def test_pre_translated_without_a_reporting_currency_is_refused():
    with pytest.raises(ValueError, match="reporting currency must be named"):
        CashFlowSeries(
            flows=(_flow(1, 1.0, currency="USD"), _flow(2, 2.0, currency="EUR")),
            pre_translated=True,
        )


# ---------------------------------------------------------------------------
# filter / between / total
# ---------------------------------------------------------------------------


def test_filter_by_single_and_multiple_kinds():
    series = CashFlowSeries(
        flows=(
            _flow(1, -100.0, kind="capital_call"),
            _flow(2, 50.0, kind="distribution"),
            _flow(3, 10.0, kind="coupon"),
        )
    )
    assert len(series.filter(kind="coupon")) == 1
    assert series.filter(kind="Capital Call").amounts() == (-100.0,)
    assert len(series.filter(kind=["coupon", "distribution"])) == 2


def test_filter_preserves_currency_even_when_it_empties_the_series():
    series = CashFlowSeries(flows=(_flow(1, 10.0, currency="EUR"),))
    empty = series.filter(kind="capital_call")
    assert empty.is_empty
    assert empty.currency == "EUR"


def test_between_is_inclusive_on_both_ends():
    series = CashFlowSeries(flows=(_flow(1, 1.0), _flow(5, 2.0), _flow(9, 3.0)))
    window = series.between(date(2024, 1, 5), date(2024, 1, 9))
    assert window.amounts() == (2.0, 3.0)


def test_between_accepts_open_ends_and_iso_strings():
    series = CashFlowSeries(flows=(_flow(1, 1.0), _flow(5, 2.0), _flow(9, 3.0)))
    assert series.between(end="2024-01-05").amounts() == (1.0, 2.0)
    assert series.between(start="2024-01-05").amounts() == (2.0, 3.0)
    assert series.between().amounts() == (1.0, 2.0, 3.0)


def test_between_rejects_a_reversed_window():
    series = CashFlowSeries(flows=(_flow(1, 1.0),))
    with pytest.raises(ValueError, match="is after end"):
        series.between(date(2024, 1, 9), date(2024, 1, 1))


def test_total_sums_signed_amounts():
    series = CashFlowSeries(
        flows=(_flow(1, -1000.0, kind="capital_call"), _flow(2, 250.0, kind="distribution"))
    )
    assert series.total() == pytest.approx(-750.0)


def test_total_excludes_nav_marks_by_default():
    series = CashFlowSeries(
        flows=(
            _flow(1, -1000.0, kind="capital_call"),
            _flow(2, 250.0, kind="distribution"),
            _flow(3, 900.0, kind="nav"),
        )
    )
    assert series.total() == pytest.approx(-750.0)
    assert series.total(include_valuations=True) == pytest.approx(150.0)


def test_nav_flow_is_flagged_as_a_valuation():
    assert _flow(1, 900.0, kind="nav").is_valuation
    assert not _flow(1, 900.0, kind="distribution").is_valuation


# ---------------------------------------------------------------------------
# Empty series behaves sanely
# ---------------------------------------------------------------------------


def test_empty_series_is_sane():
    series = CashFlowSeries()
    assert len(series) == 0
    assert series.is_empty
    assert series.dates() == ()
    assert series.amounts() == ()
    assert series.kinds() == ()
    assert series.total() == 0.0
    assert series.total(include_valuations=True) == 0.0
    assert list(series) == []
    assert series.currency is None
    assert series.filter(kind="coupon").is_empty
    assert series.between("2024-01-01", "2024-12-31").is_empty


def test_empty_series_mean_style_arithmetic_does_not_divide_by_zero():
    """Guard the obvious downstream footgun: total over an empty series."""
    series = CashFlowSeries()
    count = len(series)
    average = series.total() / count if count else 0.0
    assert average == 0.0


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


def test_load_cashflows_reads_a_fixture_csv(tmp_path):
    path = _write_csv(
        tmp_path,
        "flows.csv",
        "date,amount,kind,currency\n"
        "2024-03-31,-1000.00,capital_call,USD\n"
        "2024-01-15,-500.00,capital_call,USD\n"
        "2024-06-30,250.00,distribution,USD\n",
    )
    series = load_cashflows(path)
    assert len(series) == 3
    assert series.currency == "USD"
    assert series.dates() == (date(2024, 1, 15), date(2024, 3, 31), date(2024, 6, 30))
    assert series.total() == pytest.approx(-1250.0)
    assert series.filter(kind="distribution").total() == pytest.approx(250.0)


def test_load_cashflows_preserves_unmapped_columns_as_metadata(tmp_path):
    path = _write_csv(
        tmp_path,
        "flows.csv",
        "date,amount,kind,currency,notice_id,fund\n"
        "2024-03-31,-1000.00,capital_call,USD,NOT-17,Fund II\n",
    )
    series = load_cashflows(path)
    flow = series[0]
    assert flow.metadata["notice_id"] == "NOT-17"
    assert flow.metadata["fund"] == "Fund II"
    assert flow.metadata["source_row"] == 1
    assert flow.metadata["source_file"] == str(path)


def test_load_cashflows_missing_required_column_raises_not_none(tmp_path):
    path = _write_csv(
        tmp_path, "flows.csv", "date,kind,currency\n2024-03-31,coupon,USD\n"
    )
    with pytest.raises(CashFlowIngestError) as excinfo:
        load_cashflows(path)
    message = str(excinfo.value)
    assert "amount" in message
    assert "date, kind, currency" in message  # tells the user what it did see


def test_load_cashflows_missing_currency_is_an_error_not_a_default(tmp_path):
    path = _write_csv(tmp_path, "flows.csv", "date,amount,kind\n2024-03-31,10,coupon\n")
    with pytest.raises(CashFlowIngestError, match="currency"):
        load_cashflows(path)


def test_load_cashflows_currency_argument_substitutes_for_a_missing_column(tmp_path):
    path = _write_csv(tmp_path, "flows.csv", "date,amount,kind\n2024-03-31,10,coupon\n")
    series = load_cashflows(path, currency="EUR")
    assert series.currency == "EUR"
    assert series[0].currency == "EUR"


def test_load_cashflows_missing_kind_is_an_error_unless_defaulted(tmp_path):
    path = _write_csv(tmp_path, "flows.csv", "date,amount,currency\n2024-03-31,10,USD\n")
    with pytest.raises(CashFlowIngestError, match="default_kind"):
        load_cashflows(path)
    series = load_cashflows(path, default_kind="coupon")
    assert series[0].kind == "coupon"


def test_load_cashflows_explicit_column_mapping(tmp_path):
    path = _write_csv(
        tmp_path,
        "flows.csv",
        "Payment Date,Net Cash,Description\n2024-03-31,-1000,Drawdown 4\n",
    )
    series = load_cashflows(
        path,
        columns={"date": "Payment Date", "amount": "Net Cash"},
        currency="USD",
        default_kind="capital_call",
    )
    assert series[0].amount == -1000.0
    assert series[0].metadata["Description"] == "Drawdown 4"


def test_load_cashflows_rejects_a_mapping_to_an_absent_column(tmp_path):
    path = _write_csv(tmp_path, "flows.csv", "date,amount,currency\n2024-03-31,10,USD\n")
    with pytest.raises(CashFlowIngestError, match="not in the file"):
        load_cashflows(path, columns={"amount": "Nope"}, default_kind="coupon")


def test_load_cashflows_header_aliases_are_matched_case_insensitively(tmp_path):
    path = _write_csv(
        tmp_path,
        "flows.csv",
        "Value Date,Cash Flow,Type,CCY\n2024-03-31,-1000,Capital Call,usd\n",
    )
    series = load_cashflows(path)
    assert series[0].kind == "capital_call"
    assert series[0].currency == "USD"


def test_load_cashflows_parses_accounting_negatives_and_separators(tmp_path):
    path = _write_csv(
        tmp_path,
        "flows.csv",
        'date,amount,kind,currency\n2024-03-31,"(1,234.50)",capital_call,USD\n',
    )
    series = load_cashflows(path)
    assert series[0].amount == pytest.approx(-1234.50)


def test_load_cashflows_reads_european_grouping_without_mangling_it(tmp_path):
    """1.234,50 must be 1234.50, not 1.2345."""
    path = _write_csv(
        tmp_path,
        "flows.csv",
        'date,amount,kind,currency\n2024-03-31,"1.234,50",coupon,USD\n',
    )
    assert load_cashflows(path)[0].amount == pytest.approx(1234.50)


def test_load_cashflows_refuses_an_ambiguous_single_comma(tmp_path):
    """'1,234' is 1234 in a US export and 1.234 in a European one."""
    path = _write_csv(
        tmp_path, "flows.csv", 'date,amount,kind,currency\n2024-03-31,"1,234",coupon,USD\n'
    )
    with pytest.raises(CashFlowIngestError, match="decimal_separator"):
        load_cashflows(path)
    assert load_cashflows(path, decimal_separator=".")[0].amount == pytest.approx(1234.0)
    assert load_cashflows(path, decimal_separator=",")[0].amount == pytest.approx(1.234)


def test_load_cashflows_rejects_an_invalid_decimal_separator(tmp_path):
    path = _write_csv(
        tmp_path, "flows.csv", "date,amount,kind,currency\n2024-03-31,10,coupon,USD\n"
    )
    with pytest.raises(ValueError, match="decimal_separator must be"):
        load_cashflows(path, decimal_separator=";")


def test_load_cashflows_strips_currency_symbols_and_nbsp(tmp_path):
    path = _write_csv(
        tmp_path,
        "flows.csv",
        'date,amount,kind,currency\n2024-03-31,"$ 1 234.50",coupon,USD\n',
    )
    assert load_cashflows(path)[0].amount == pytest.approx(1234.50)


def test_load_cashflows_invert_sign_restores_the_convention(tmp_path):
    """A file reporting calls as positive is corrected at the boundary."""
    path = _write_csv(
        tmp_path, "flows.csv", "date,amount,kind,currency\n2024-03-31,1000,capital_call,USD\n"
    )
    with pytest.raises(CashFlowIngestError, match="negative"):
        load_cashflows(path)
    series = load_cashflows(path, invert_sign=True)
    assert series[0].amount == -1000.0


def test_load_cashflows_requires_explicit_format_for_non_iso_dates(tmp_path):
    path = _write_csv(
        tmp_path, "flows.csv", "date,amount,kind,currency\n31/03/2024,10,coupon,USD\n"
    )
    with pytest.raises(CashFlowIngestError, match="date_format"):
        load_cashflows(path)
    series = load_cashflows(path, date_format="%d/%m/%Y")
    assert series[0].date == date(2024, 3, 31)


def test_load_cashflows_blank_amount_is_an_error_not_zero(tmp_path):
    path = _write_csv(
        tmp_path, "flows.csv", "date,amount,kind,currency\n2024-03-31,,coupon,USD\n"
    )
    with pytest.raises(CashFlowIngestError, match="blank"):
        load_cashflows(path)


def test_load_cashflows_reports_the_offending_row_number(tmp_path):
    path = _write_csv(
        tmp_path,
        "flows.csv",
        "date,amount,kind,currency\n"
        "2024-01-31,10,coupon,USD\n"
        "2024-02-29,oops,coupon,USD\n",
    )
    with pytest.raises(CashFlowIngestError, match="row 2"):
        load_cashflows(path)


def test_load_cashflows_missing_file_raises_ingest_error(tmp_path):
    with pytest.raises(CashFlowIngestError, match="not found"):
        load_cashflows(tmp_path / "absent.csv")


def test_load_cashflows_unsupported_suffix_is_explicit(tmp_path):
    path = _write_csv(tmp_path, "flows.xlsx", "date,amount\n")
    with pytest.raises(CashFlowIngestError, match="unsupported file type"):
        load_cashflows(path, currency="USD", default_kind="coupon")


def test_load_cashflows_header_only_file_yields_an_empty_series(tmp_path):
    path = _write_csv(tmp_path, "flows.csv", "date,amount,kind,currency\n")
    series = load_cashflows(path, currency="USD")
    assert series.is_empty
    assert series.total() == 0.0


def test_load_cashflows_mixed_currency_file_is_refused(tmp_path):
    path = _write_csv(
        tmp_path,
        "flows.csv",
        "date,amount,kind,currency\n"
        "2024-03-31,100,coupon,USD\n"
        "2024-04-30,100,coupon,EUR\n",
    )
    with pytest.raises(CurrencyMismatchError):
        load_cashflows(path)


def test_load_cashflows_tsv_delimiter_inferred_from_suffix(tmp_path):
    path = _write_csv(
        tmp_path, "flows.tsv", "date\tamount\tkind\tcurrency\n2024-03-31\t10\tcoupon\tUSD\n"
    )
    series = load_cashflows(path)
    assert series[0].amount == 10.0


def test_load_cashflows_skips_trailing_blank_line(tmp_path):
    path = _write_csv(
        tmp_path,
        "flows.csv",
        "date,amount,kind,currency\n2024-03-31,10,coupon,USD\n,,,\n",
    )
    assert len(load_cashflows(path)) == 1


# ---------------------------------------------------------------------------
# Entity / instrument models
# ---------------------------------------------------------------------------


def test_entity_constructs_and_is_immutable():
    entity = Entity(entity_id="LEI-123", name="Acme Capital", entity_type="manager")
    assert entity.entity_type is EntityType.MANAGER
    with pytest.raises(dataclasses.FrozenInstanceError):
        entity.name = "Other"


def test_entity_rejects_blank_id_and_unknown_type():
    with pytest.raises(ValueError, match="entity_id is required"):
        Entity(entity_id="   ")
    with pytest.raises(ValueError, match="unknown entity_type"):
        Entity(entity_id="E1", entity_type="wizard")


def test_entity_cannot_be_its_own_parent():
    with pytest.raises(ValueError, match="own parent"):
        Entity(entity_id="E1", parent_id="E1")


def test_instrument_requires_currency_and_normalizes_it():
    instrument = Instrument(instrument_id="X1", currency="usd")
    assert instrument.currency == "USD"
    with pytest.raises(ValueError, match="currency is required"):
        Instrument(instrument_id="X1", currency="")


def test_instrument_accepts_a_crypto_quote_asset():
    """Currency is not constrained to three letters; USDT is real here."""
    assert Instrument(instrument_id="BTC-USDT", currency="USDT").currency == "USDT"


def test_security_carries_venue_details():
    issuer = Entity(entity_id="CIK-320193", name="Apple Inc.")
    security = Security(
        instrument_id="US0378331005",
        currency="USD",
        symbol="AAPL",
        exchange="XNAS",
        security_type="etf",
        issuer=issuer,
    )
    assert security.security_type is SecurityType.ETF
    assert security.issuer.name == "Apple Inc."
    assert isinstance(security, Instrument)


def test_fund_validates_its_economics():
    fund = Fund(
        instrument_id="FUND-II",
        currency="EUR",
        vintage_year=2021,
        structure="closed_end",
        commitment=25_000_000,
        management_fee_rate=0.02,
    )
    assert fund.structure is FundStructure.CLOSED_END
    assert fund.commitment == 25_000_000.0
    with pytest.raises(ValueError, match="non-negative"):
        Fund(instrument_id="F", currency="EUR", commitment=-1)
    with pytest.raises(ValueError, match="decimal fraction"):
        Fund(instrument_id="F", currency="EUR", management_fee_rate=2.0)
    with pytest.raises(ValueError, match="vintage_year"):
        Fund(instrument_id="F", currency="EUR", vintage_year=12)


def test_bond_validates_coupon_and_maturity():
    bond = Bond(
        instrument_id="US912828",
        currency="USD",
        face_value=1000,
        coupon_rate=0.045,
        coupon_frequency=2,
        inception_date="2020-01-15",
        maturity_date="2030-01-15",
    )
    assert bond.maturity_date == date(2030, 1, 15)
    assert bond.coupon_rate == pytest.approx(0.045)

    with pytest.raises(ValueError, match="percentage"):
        Bond(instrument_id="B", currency="USD", coupon_rate=4.5)
    with pytest.raises(ValueError, match="face_value must be positive"):
        Bond(instrument_id="B", currency="USD", face_value=0)
    with pytest.raises(ValueError, match="zero-coupon"):
        Bond(instrument_id="B", currency="USD", coupon_rate=0.05, coupon_frequency=0)
    with pytest.raises(ValueError, match="precedes inception"):
        Bond(
            instrument_id="B",
            currency="USD",
            inception_date="2030-01-01",
            maturity_date="2020-01-01",
        )


def test_zero_coupon_bond_is_valid():
    bond = Bond(instrument_id="Z", currency="USD", coupon_rate=0.0, coupon_frequency=0)
    assert bond.coupon_frequency == 0


# ---------------------------------------------------------------------------
# The parallel-path guarantee
# ---------------------------------------------------------------------------


def test_spine_does_not_widen_the_bar_price_panel():
    """A nav must never become a column a bar engine could price as a close."""
    from backtest.runner import _PRICE_PANEL_COLUMNS, _VALID_INTERVALS

    assert _PRICE_PANEL_COLUMNS == ("open", "high", "low", "close", "volume", "vwap", "amount")
    assert "nav" not in _PRICE_PANEL_COLUMNS
    assert _VALID_INTERVALS == {"1m", "5m", "15m", "30m", "1H", "4H", "1D"}


def test_series_is_not_a_dataframe_and_exposes_no_bar_fields():
    series = CashFlowSeries(flows=(_flow(1, 100.0, kind="nav"),))
    assert not hasattr(series, "close")
    assert not hasattr(series[0], "close")
