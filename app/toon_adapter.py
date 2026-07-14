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


def toon_round_trip_matches(value: Any, encoded: str) -> bool:
    try:
        from toon_format import decode

        decoded = decode(encoded)
    except Exception:
        return False
    return _typed_value(decoded) == _typed_value(value)


def _typed_value(value: Any) -> Any:
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", value)
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, list):
        return ("list", tuple(_typed_value(item) for item in value))
    if isinstance(value, dict):
        return (
            "dict",
            tuple((key, _typed_value(child)) for key, child in value.items()),
        )
    return (type(value).__name__, repr(value))
