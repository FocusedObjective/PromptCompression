from app.toon_adapter import encode_toon


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
