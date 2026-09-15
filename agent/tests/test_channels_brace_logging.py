"""Channel loggers render loguru-style ``{}`` messages without logging errors.

Regression tests for the stdlib-logger / ``{}``-placeholder mismatch in the
channel adapters: every such record used to raise ``TypeError`` inside
``LogRecord.getMessage`` and be dropped via ``Handler.handleError``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from src.channels.brace_logging import BraceStyleAdapter, get_channel_logger
from src.channels.bus.queue import MessageBus


class _RecordingHandler(logging.Handler):
    """Formats every record (like a real handler) and records handleError calls."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter("%(levelname)s %(funcName)s:%(lineno)d %(message)s"))
        self.records: list[logging.LogRecord] = []
        self.lines: list[str] = []
        self.errors: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
            self.records.append(record)
        except Exception:
            self.handleError(record)

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - stdlib name
        self.errors.append(record)


@pytest.fixture
def capture() -> Iterator[tuple[_RecordingHandler, str]]:
    """Attach a recording handler to a dedicated logger at DEBUG."""
    name = "src.channels.base.brace-test"
    base = logging.getLogger(name)
    handler = _RecordingHandler()
    old_level, old_propagate = base.level, base.propagate
    base.addHandler(handler)
    base.setLevel(logging.DEBUG)
    base.propagate = False
    try:
        yield handler, name
    finally:
        base.removeHandler(handler)
        base.setLevel(old_level)
        base.propagate = old_propagate


def test_brace_and_percent_messages_both_render(capture: tuple[_RecordingHandler, str]) -> None:
    handler, name = capture
    log = get_channel_logger(name)

    log.info("sent to {} in {}", "alice", "room-1")
    log.warning("percent style %s=%d", "count", 3)
    log.info("mapping style %(who)s", {"who": "bob"})
    log.info("literal braces {} with no args")
    log.error("dict literal {'k': %s}", 7)  # brace-looking %s message falls back to %
    log.info("value {} contains a percent", "100%")

    assert [r.getMessage() for r in handler.records] == [
        "sent to alice in room-1",
        "percent style count=3",
        "mapping style bob",
        "literal braces {} with no args",
        "dict literal {'k': 7}",
        "value 100% contains a percent",
    ]
    assert handler.errors == []


def test_no_typeerror_reaches_logging_error_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logging, "raiseExceptions", True)
    # Unparented Logger: only our recording handler sees these records.
    raw = logging.Logger("brace-isolated", level=logging.DEBUG)
    handler = _RecordingHandler()
    raw.addHandler(handler)

    # Control: the plain stdlib logger hits handleError for a {} call.
    raw.info("raw stdlib {} {}", "a", "b")
    assert len(handler.errors) == 1
    assert handler.records == []
    handler.errors.clear()

    BraceStyleAdapter(raw).info("adapter {} {}", "a", "b")
    assert handler.errors == []
    assert [r.getMessage() for r in handler.records] == ["adapter a b"]


def test_exception_and_loguru_opt_carry_exc_info(capture: tuple[_RecordingHandler, str]) -> None:
    handler, name = capture
    log = get_channel_logger(name)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("failed sending {}", "file.png")
        log.opt(exception=True).error("Error in {}: {}", "receive", "boom")
        log.opt(exception=True).warning("Network error sending media {}", "m.jpg")

    msgs = [(r.levelno, r.getMessage(), r.exc_info is not None) for r in handler.records]
    assert msgs == [
        (logging.ERROR, "failed sending file.png", True),
        (logging.ERROR, "Error in receive: boom", True),
        (logging.WARNING, "Network error sending media m.jpg", True),
    ]
    assert handler.errors == []


def test_caller_location_is_the_call_site(capture: tuple[_RecordingHandler, str]) -> None:
    handler, name = capture
    log = get_channel_logger(name)
    log.info("where {}", "here")
    log.opt(exception=False).info("where {}", "opt")
    for record in handler.records:
        assert record.funcName == "test_caller_location_is_the_call_site"
        assert record.pathname.endswith("test_channels_brace_logging.py")


def test_disabled_level_is_skipped(capture: tuple[_RecordingHandler, str]) -> None:
    handler, name = capture
    logging.getLogger(name).setLevel(logging.WARNING)
    get_channel_logger(name).info("dropped {}", "x")
    assert handler.records == []


def test_channel_loggers_use_the_brace_adapter() -> None:
    from src.channels import email, napcat, qq, weixin
    from src.channels.signal import SignalChannel, SignalConfig

    channel = SignalChannel(SignalConfig(enabled=True, phone_number="+15550000"), MessageBus())
    assert isinstance(channel.logger, BraceStyleAdapter)
    assert channel.logger.name == "src.channels.base.signal"
    for module in (email, napcat, qq, weixin):
        assert isinstance(module.logger, BraceStyleAdapter), module.__name__


def test_signal_denied_pairing_audit_line_is_emitted_at_info() -> None:
    """The GHSA-fwpw denial audit line is actually written, not dropped."""
    from src.channels.signal import SignalChannel, SignalConfig, SignalGroupConfig

    config = SignalConfig(
        enabled=True,
        phone_number="+15550000",
        group=SignalGroupConfig(enabled=True, policy="allowlist", allow_from=["group-1"]),
    )
    channel = SignalChannel(config, MessageBus())
    base = logging.getLogger("src.channels.base.signal")
    handler = _RecordingHandler()
    old_level = base.level
    base.addHandler(handler)
    base.setLevel(logging.INFO)
    try:
        allowed, _ = channel._check_inbound_policy(
            sender_id="+15559999",
            sender_number="+15559999",
            group_id="group-1",
            is_group_message=True,
            message_text="/pairing list",
            mentions=[],
            sender_name="Mallory",
            timestamp=None,
        )
    finally:
        base.removeHandler(handler)
        base.setLevel(old_level)

    assert allowed is False
    assert handler.errors == []
    audit = [r for r in handler.records if "control-plane" in r.getMessage()]
    assert len(audit) == 1
    assert audit[0].levelno == logging.INFO
    assert audit[0].getMessage() == (
        "Ignoring group control-plane command from unauthorized sender +15559999 in group-1"
    )
    assert audit[0].funcName == "_check_inbound_policy"
