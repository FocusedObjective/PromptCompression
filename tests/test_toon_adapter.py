from app.toon_adapter import encode_toon, toon_round_trip_matches


def test_encode_toon_uses_python_toon_library():
    toon = encode_toon(
        {
            "users": [
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
            ]
        }
    )

    assert "users[2]{id,name}:" in toon
    assert "1,Alice" in toon
    assert "2,Bob" in toon


def test_toon_round_trip_preserves_json_types_and_order():
    value = {
        "rows": [
            {"id": 1, "active": True, "code": "001", "note": None},
            {"id": 2, "active": False, "code": "002", "note": "café"},
        ]
    }

    encoded = encode_toon(value)

    assert toon_round_trip_matches(value, encoded) is True
    assert toon_round_trip_matches({"value": 1}, "value: true") is False
