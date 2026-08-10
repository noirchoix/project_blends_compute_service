from __future__ import annotations

import logging
import re
import threading
from contextlib import contextmanager
from typing import Iterator

from rdkit import rdBase


_LOCK = threading.RLock()


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[dict[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage().strip()
        if message:
            self.records.append(
                {
                    "level": record.levelname.lower(),
                    "code": normalize_rdkit_message(message),
                    "message": message,
                }
            )


def normalize_rdkit_message(message: str) -> str:
    text = re.sub(r"^\[\d\d:\d\d:\d\d\]\s*", "", message).strip()
    text = re.sub(r"^(WARNING|ERROR):\s*", "", text, flags=re.IGNORECASE).strip()
    lowered = text.casefold()
    known = {
        "omitted undefined stereo": "omitted_undefined_stereo",
        "charges were rearranged": "charges_rearranged",
    }
    for phrase, code in known.items():
        if phrase in lowered:
            return code
    if "conflicting single bond directions" in lowered:
        return "conflicting_bond_stereo_directions"
    if "smiles parse error" in lowered:
        return "smiles_parse_error"
    token = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    return token[:96] or "rdkit_message"


@contextmanager
def capture_rdkit_messages() -> Iterator[list[dict[str, str]]]:
    """Capture RDKit warnings/errors as structured QC instead of console spam.

    RDKit logging backends are process-global, so the short backend swap is protected
    by a re-entrant lock. The original C++ stream backend is restored immediately.
    """
    with _LOCK:
        logger = logging.getLogger("rdkit")
        old_handlers = list(logger.handlers)
        old_level = logger.level
        old_propagate = logger.propagate
        handler = _CaptureHandler()
        try:
            rdBase.LogToPythonLogger()
            logger.handlers = [handler]
            logger.setLevel(logging.WARNING)
            logger.propagate = False
            yield handler.records
        finally:
            logger.handlers = old_handlers
            logger.setLevel(old_level)
            logger.propagate = old_propagate
            rdBase.LogToCppStreams()
