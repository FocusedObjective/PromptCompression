from typing import Any


class ToonEncodingError(RuntimeError):
    """Raised when JSON cannot be encoded as TOON."""


def encode_toon(value: Any) -> str:
    """Encode a JSON-compatible Python value using the TOON Python library."""
    try:
        from toon_format import encode
    except ImportError as exc:
        raise ToonEncodingError(
            "toon_format is not installed. Install dependencies from requirements.txt."
        ) from exc

    try:
        return encode(value)
    except Exception as exc:
        raise ToonEncodingError(f"TOON encoding failed: {exc}") from exc
