"""Pluggable OCR engine interface and factory.

Allows users to swap between local OCR (RapidOCR) and cloud vision models
(Qwen-VL, etc.) via environment variable VIBE_TRADING_OCR_ENGINE.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)

_OCR_ENGINE_ENV = "VIBE_TRADING_OCR_ENGINE"
_VALID_ENGINES = ("auto", "rapid", "qwen-vl", "none")


@runtime_checkable
class OcrEngine(Protocol):
    """Abstract OCR engine interface.

    All implementations must accept a numpy RGB image array and return
    extracted text (empty string if no text found).
    """

    name: str

    def is_available(self) -> bool:
        """Check if this engine's dependencies are satisfied."""
        ...

    def recognize(self, image: np.ndarray) -> str:
        """Run OCR on a numpy RGB image array. Return extracted text."""
        ...


def get_ocr_engine() -> OcrEngine | None:
    """Return the configured OCR engine, or None if unavailable.

    Engine selection via VIBE_TRADING_OCR_ENGINE:
      - "auto" (default): try rapid first, then qwen-vl
      - "rapid": RapidOCR only (local, ONNX)
      - "qwen-vl": Qwen-VL vision model (cloud, DashScope API)
      - "none": disable OCR entirely
    """
    choice = os.getenv(_OCR_ENGINE_ENV, "auto").strip().lower()
    if choice not in _VALID_ENGINES:
        logger.warning("Unknown OCR engine '%s', falling back to 'auto'", choice)
        choice = "auto"

    if choice == "none":
        return None

    if choice == "rapid":
        return _try_rapid()

    if choice == "qwen-vl":
        return _try_qwen_vl()

    engines_to_try = [(_try_rapid, "rapid"), (_try_qwen_vl, "qwen-vl")]
    for factory, name in engines_to_try:
        try:
            engine = factory()
            if engine is not None and engine.is_available():
                logger.info("OCR engine selected: %s", name)
                return engine
        except Exception:
            logger.debug("OCR engine '%s' not available", name, exc_info=True)

    return None


def _try_rapid() -> OcrEngine | None:
    try:
        from src.tools.ocr.rapid_ocr import RapidOcrEngine
        engine = RapidOcrEngine()
        if engine.is_available():
            return engine
    except ImportError:
        pass
    return None


def _try_qwen_vl() -> OcrEngine | None:
    try:
        from src.tools.ocr.qwen_vision_ocr import QwenVisionOcrEngine
        engine = QwenVisionOcrEngine()
        if engine.is_available():
            return engine
    except Exception:
        pass
    return None


def get_ocr_install_hint(engine: OcrEngine | None) -> str:
    """Return an actionable install message for the missing OCR engine."""
    if engine is not None:
        return ""

    choice = os.getenv(_OCR_ENGINE_ENV, "auto").strip().lower()

    if choice == "qwen-vl":
        return (
            "Qwen-VL OCR engine requires DASHSCOPE_API_KEY environment variable. "
            "Get a key from https://dashscope.console.aliyun.com/ and set: "
            "export DASHSCOPE_API_KEY=your_key"
        )

    if choice == "rapid":
        return (
            "RapidOCR engine not installed. Install with: "
            "pip install rapidocr_onnxruntime"
        )

    return (
        "No OCR engine available. Install one of:\n"
        "  1. Local OCR: pip install rapidocr_onnxruntime\n"
        "  2. Cloud vision: export DASHSCOPE_API_KEY=your_key (uses Qwen-VL)\n"
        "Or set VIBE_TRADING_OCR_ENGINE=none to disable OCR."
    )
