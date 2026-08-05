"""Read irregular dated cash-flow files into a ``CashFlowSeries``.

This is the entry point a user's own file takes into the system. It is a
deliberately separate path from ``backtest/loaders``: that path requires an
OHLC bar and drops every column it does not recognise, which is precisely why a
capital call or a coupon schedule cannot get in through it.

Two rules govern this module:

* **Never return None.** A malformed or mis-mapped file raises
  ``CashFlowIngestError`` naming the file, the row, and the fix. The bar loader
  returns ``None`` for an unrecognised file and the caller sees "no data" with
  no idea why; that failure mode is not repeated here.
* **Never guess.** An ambiguous date format, an ambiguous decimal separator, a
  missing currency, or an unmappable kind is an error, not an inference.
  Guessing day-first vs month-first, or reading ``1.234,50`` as ``1.2345``,
  produces a plausible wrong answer that nothing downstream can detect.

Unmapped columns are preserved into ``CashFlow.metadata`` rather than dropped.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.entities.cashflow import CashFlow, CashFlowSeries
from src.entities.models import normalize_date

logger = logging.getLogger(__name__)

__all__ = ["CashFlowIngestError", "load_cashflows"]

#: Canonical field -> the column names accepted for it without an explicit
#: mapping. Matching is case-insensitive and ignores spaces and underscores.
DEFAULT_COLUMN_ALIASES: Mapping[str, tuple[str, ...]] = {
    "date": ("date", "asof", "asofdate", "transactiondate", "valuedate", "paymentdate"),
    "amount": ("amount", "cashflow", "cash", "value", "net", "netamount"),
    "kind": ("kind", "type", "cashflowtype", "transactiontype", "category", "flowtype"),
    "currency": ("currency", "ccy", "curr", "currencycode"),
}

#: Delimiter inferred from the file suffix when the caller does not name one.
_DELIMITER_BY_SUFFIX: Mapping[str, str] = {".csv": ",", ".tsv": "\t", ".txt": ","}

#: Characters removed from a numeric field before parsing: whitespace (including
#: the non-breaking space used by many exports), the Swiss group separator, and
#: currency symbols. Note that ``,`` and ``.`` are deliberately absent -- which
#: one is the decimal separator is resolved per value in ``_parse_amount``,
#: because deleting the wrong one silently turns ``1.234,50`` into ``1.2345``.
_AMOUNT_SYMBOLS = frozenset(" \t\u00a0'$€£¥")

#: Decimal separators a caller may declare explicitly.
_DECIMAL_SEPARATORS = (".", ",")


class CashFlowIngestError(ValueError):
    """Raised when a cash-flow file cannot be read into a valid series."""


def _canonical(name: str) -> str:
    """Reduce a column header to its comparison form.

    Args:
        name: Raw header text.

    Returns:
        Lower-cased header with spaces, underscores, and hyphens removed.
    """
    return name.strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _resolve_columns(
    header: Sequence[str],
    columns: Mapping[str, str] | None,
    path: Path,
) -> dict[str, str]:
    """Map canonical field names onto this file's actual column headers.

    Args:
        header: Column names as they appear in the file.
        columns: Explicit overrides, canonical field -> file column name.
        path: File path, used only for error messages.

    Returns:
        Mapping of canonical field name to the file's column name, containing
        only fields that were actually found.

    Raises:
        CashFlowIngestError: If an explicit override names a column that the
            file does not contain, or overrides an unknown field.
    """
    resolved: dict[str, str] = {}
    lookup = {_canonical(col): col for col in header}

    if columns:
        for field_name, source_name in columns.items():
            if field_name not in DEFAULT_COLUMN_ALIASES:
                known = ", ".join(sorted(DEFAULT_COLUMN_ALIASES))
                raise CashFlowIngestError(
                    f"{path}: unknown column mapping {field_name!r}; "
                    f"mappable fields are: {known}"
                )
            if source_name not in header:
                raise CashFlowIngestError(
                    f"{path}: mapped column {source_name!r} for field "
                    f"{field_name!r} is not in the file. Columns present: "
                    f"{', '.join(header)}"
                )
            resolved[field_name] = source_name

    for field_name, aliases in DEFAULT_COLUMN_ALIASES.items():
        if field_name in resolved:
            continue
        for alias in aliases:
            if alias in lookup:
                resolved[field_name] = lookup[alias]
                break

    return resolved


def _to_plain_number(text: str, decimal_separator: str | None) -> str:
    """Reduce a grouped decimal string to a form ``float`` accepts.

    Args:
        text: Numeric text with symbols already removed, e.g. ``"1,234.50"``.
        decimal_separator: ``"."`` or ``","`` when the caller declared one,
            otherwise ``None`` to infer.

    Returns:
        The same number using ``.`` as the decimal separator and no grouping.

    Raises:
        ValueError: If the value uses a single comma whose role cannot be
            determined -- ``"1,234"`` is 1234 in a US export and 1.234 in a
            European one, and the data cannot say which.
    """
    if decimal_separator is not None:
        grouping = "," if decimal_separator == "." else "."
        return text.replace(grouping, "").replace(decimal_separator, ".")

    has_dot = "." in text
    has_comma = "," in text
    if has_dot and has_comma:
        # The rightmost separator is the decimal one; the other groups digits.
        if text.rindex(".") > text.rindex(","):
            return text.replace(",", "")
        return text.replace(".", "").replace(",", ".")
    if has_comma:
        if text.count(",") > 1:
            return text.replace(",", "")  # 1,234,567 cannot be a decimal comma
        raise ValueError(
            f"{text!r} uses a single comma and could be either "
            f"{text.replace(',', '')} (comma groups thousands) or "
            f"{text.replace(',', '.')} (comma is the decimal separator); pass "
            "decimal_separator='.' or decimal_separator=',' to say which"
        )
    return text


def _parse_amount(
    raw: str,
    path: Path,
    row_number: int,
    decimal_separator: str | None = None,
) -> float:
    """Parse a numeric amount from a file field.

    Handles currency symbols, digit grouping, and the accounting convention
    where parentheses or a trailing minus denote a negative number, e.g.
    ``(1,234.50)``.

    Args:
        raw: Raw cell text.
        path: File path, used only for error messages.
        row_number: 1-based data row number, used only for error messages.
        decimal_separator: Declared decimal separator, or ``None`` to infer.

    Returns:
        The parsed float, negated when the cell was parenthesised or carried a
        trailing minus.

    Raises:
        CashFlowIngestError: If the cell is blank, not numeric, or ambiguously
            grouped.
    """
    text = (raw or "").strip()
    if not text:
        raise CashFlowIngestError(
            f"{path} row {row_number}: amount is blank. A missing amount must be "
            "fixed at the source; it is not treated as zero."
        )

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = "".join(ch for ch in text if ch not in _AMOUNT_SYMBOLS).strip()
    if text.endswith("-"):  # trailing-minus exports
        text = "-" + text[:-1]

    try:
        value = float(_to_plain_number(text, decimal_separator))
    except ValueError as exc:
        raise CashFlowIngestError(
            f"{path} row {row_number}: amount {raw!r} is not usable: {exc}"
        ) from exc
    return -value if negative else value


def _parse_date(
    raw: str,
    date_format: str | None,
    path: Path,
    row_number: int,
) -> date:
    """Parse a date cell, using an explicit format when one is supplied.

    Args:
        raw: Raw cell text.
        date_format: ``strptime`` format, or ``None`` to require ISO-8601.
        path: File path, used only for error messages.
        row_number: 1-based data row number, used only for error messages.

    Returns:
        The parsed ``datetime.date``.

    Raises:
        CashFlowIngestError: If the cell is blank or does not parse.
    """
    text = (raw or "").strip()
    if not text:
        raise CashFlowIngestError(f"{path} row {row_number}: date is blank")
    if date_format:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError as exc:
            raise CashFlowIngestError(
                f"{path} row {row_number}: date {raw!r} does not match "
                f"date_format={date_format!r}"
            ) from exc
    try:
        return normalize_date(text)
    except ValueError as exc:
        raise CashFlowIngestError(
            f"{path} row {row_number}: date {raw!r} is not ISO-8601 "
            "(YYYY-MM-DD). Pass date_format=... explicitly; regional formats "
            "are not guessed because day-first and month-first cannot be told "
            "apart from the data."
        ) from exc


def _read_rows(
    path: Path, delimiter: str | None, encoding: str
) -> tuple[list[str], list[dict[str, str]]]:
    """Read a delimited text file into a header and a list of row mappings.

    Args:
        path: File to read.
        delimiter: Field delimiter, or ``None`` to infer from the suffix.
        encoding: Text encoding.

    Returns:
        Tuple of (header column names, list of row dicts).

    Raises:
        CashFlowIngestError: If the file is missing, unreadable, has an
            unsupported suffix, or has no header row.
    """
    if not path.exists():
        raise CashFlowIngestError(f"cash-flow file not found: {path}")

    if delimiter is None:
        suffix = path.suffix.lower()
        delimiter = _DELIMITER_BY_SUFFIX.get(suffix)
        if delimiter is None:
            supported = ", ".join(sorted(_DELIMITER_BY_SUFFIX))
            raise CashFlowIngestError(
                f"{path}: unsupported file type {suffix!r}; supported suffixes "
                f"are {supported}. Pass delimiter=... to read another format."
            )

    try:
        with open(path, "r", encoding=encoding, newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            header = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
    except UnicodeDecodeError as exc:
        raise CashFlowIngestError(
            f"{path}: cannot decode as {encoding!r}; pass encoding=... "
            "(exports from Chinese brokers are often 'gbk')"
        ) from exc
    except OSError as exc:
        raise CashFlowIngestError(f"{path}: cannot read file: {exc}") from exc

    if not header:
        raise CashFlowIngestError(f"{path}: file has no header row")
    return header, rows


def load_cashflows(
    path: str | Path,
    *,
    columns: Mapping[str, str] | None = None,
    currency: str | None = None,
    default_kind: str | None = None,
    date_format: str | None = None,
    decimal_separator: str | None = None,
    delimiter: str | None = None,
    encoding: str = "utf-8",
    invert_sign: bool = False,
    pre_translated: bool = False,
    metadata_columns: Iterable[str] | None = None,
) -> CashFlowSeries:
    """Load an irregular dated cash-flow file into a ``CashFlowSeries``.

    The file needs a date column and an amount column. Currency and kind may
    come either from a column or from an argument, but must come from
    somewhere: neither is defaulted, because a wrong currency or a bucket
    labelled "unknown" silently corrupts every figure computed downstream.

    Columns that are not mapped to a canonical field are preserved verbatim in
    each ``CashFlow.metadata``.

    Args:
        path: Path to the file. ``.csv``, ``.tsv``, and ``.txt`` are recognised;
            any other suffix requires an explicit ``delimiter``.
        columns: Explicit mapping of canonical field to file column name, e.g.
            ``{"date": "Payment Date", "amount": "Net"}``. Canonical fields are
            ``date``, ``amount``, ``kind``, and ``currency``. Unmapped fields
            fall back to the aliases in ``DEFAULT_COLUMN_ALIASES``.
        currency: Currency for every row. Required when the file has no
            currency column; when both are present this argument names the
            series' reporting currency and the column supplies each row's.
        default_kind: Kind for rows whose kind column is absent or blank.
        date_format: ``strptime`` format for the date column. Omit only when
            the dates are ISO-8601.
        decimal_separator: ``"."`` or ``","``. Needed only for values whose
            grouping is genuinely ambiguous, such as a bare ``"1,234"``.
        delimiter: Field delimiter; inferred from the suffix when omitted.
        encoding: Text encoding of the file.
        invert_sign: Negate every amount after parsing. Use for files that
            report calls as positive, so the holder-perspective convention in
            ``src.entities.cashflow`` is restored at the boundary rather than
            by each caller downstream.
        pre_translated: Assert that a multi-currency file has already been
            converted into ``currency``. Requires ``currency``.
        metadata_columns: Restrict preserved metadata to these columns. By
            default every unmapped column is preserved.

    Returns:
        A ``CashFlowSeries`` ordered by date. An input file with a header but
        no data rows yields an empty series rather than an error.

    Raises:
        CashFlowIngestError: If the file is missing or unreadable, a required
            column is absent, currency or kind cannot be determined, or any row
            fails to parse or violates the sign convention. Never returns None.
        CurrencyMismatchError: If the file's rows span several currencies and
            ``pre_translated`` was not set.
        ValueError: If ``decimal_separator`` is neither ``"."`` nor ``","``.
    """
    if decimal_separator is not None and decimal_separator not in _DECIMAL_SEPARATORS:
        raise ValueError(
            f"decimal_separator must be one of {_DECIMAL_SEPARATORS}, "
            f"got {decimal_separator!r}"
        )

    path = Path(path).expanduser()
    header, rows = _read_rows(path, delimiter, encoding)
    resolved = _resolve_columns(header, columns, path)

    for required in ("date", "amount"):
        if required not in resolved:
            aliases = ", ".join(DEFAULT_COLUMN_ALIASES[required])
            raise CashFlowIngestError(
                f"{path}: required column {required!r} not found. Columns "
                f"present: {', '.join(header)}. Recognised names: {aliases}. "
                f"Pass columns={{{required!r}: '<your column>'}} to map it."
            )

    currency_column = resolved.get("currency")
    if currency_column is None and currency is None:
        raise CashFlowIngestError(
            f"{path}: no currency column found and no currency=... given. "
            "Currency is required; it is never defaulted, because summing "
            "across currencies produces a plausible wrong number."
        )

    kind_column = resolved.get("kind")
    if kind_column is None and default_kind is None:
        raise CashFlowIngestError(
            f"{path}: no kind column found and no default_kind=... given. "
            f"Columns present: {', '.join(header)}."
        )

    mapped_columns = set(resolved.values())
    if metadata_columns is None:
        extra_columns = [col for col in header if col not in mapped_columns]
    else:
        wanted = set(metadata_columns)
        missing = wanted - set(header)
        if missing:
            raise CashFlowIngestError(
                f"{path}: metadata_columns not in file: {', '.join(sorted(missing))}"
            )
        extra_columns = [col for col in header if col in wanted]

    flows: list[CashFlow] = []
    for offset, row in enumerate(rows):
        row_number = offset + 1
        if all(not (value or "").strip() for value in row.values()):
            continue  # trailing blank line

        amount = _parse_amount(
            row.get(resolved["amount"], ""), path, row_number, decimal_separator
        )
        if invert_sign:
            amount = -amount

        row_currency = currency
        if currency_column is not None:
            raw_currency = (row.get(currency_column) or "").strip()
            if raw_currency:
                row_currency = raw_currency
        if not row_currency:
            raise CashFlowIngestError(
                f"{path} row {row_number}: currency is blank and no currency=... "
                "fallback was given"
            )

        row_kind = default_kind
        if kind_column is not None:
            raw_kind = (row.get(kind_column) or "").strip()
            if raw_kind:
                row_kind = raw_kind
        if not row_kind:
            raise CashFlowIngestError(
                f"{path} row {row_number}: kind is blank and no default_kind=... "
                "fallback was given"
            )

        metadata: dict[str, Any] = {
            col: row[col] for col in extra_columns if (row.get(col) or "").strip()
        }
        metadata["source_file"] = str(path)
        metadata["source_row"] = row_number

        try:
            flows.append(
                CashFlow(
                    date=_parse_date(
                        row.get(resolved["date"], ""), date_format, path, row_number
                    ),
                    amount=amount,
                    kind=row_kind,
                    currency=row_currency,
                    metadata=metadata,
                )
            )
        except CashFlowIngestError:
            raise
        except ValueError as exc:
            raise CashFlowIngestError(f"{path} row {row_number}: {exc}") from exc

    logger.info("loaded %d cash flows from %s", len(flows), path)
    return CashFlowSeries(
        flows=tuple(flows),
        currency=currency,
        pre_translated=pre_translated,
    )
