"""Keep personal and share keys out of log output."""

import logging
import re

_KEY_PATH = re.compile(r"(/[us]/)[^/?#\s]+")


def redact_keys(text: str) -> str:
    """Replace the key in `/u/<key>` and `/s/<key>` so links never reach the logs."""
    return _KEY_PATH.sub(r"\1***", text)


class RedactingFilter(logging.Filter):
    """Redacts keys in a record's message and string arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_keys(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_keys(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact_keys(v) if isinstance(v, str) else v for k, v in record.args.items()}
        return True
