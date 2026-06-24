import json
import os
import sys

import requests

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000/compress")

PAYLOAD = {
    "aggressiveness": 0.25,
    "text": """
PromptOpsKit
Why
Demo
Example
Docs
GitHub
Open-source prompt infrastructure

Prompts are production code. Manage them that way.
AI features start with a few prompt strings. Then model settings, tools,
provider quirks, context limits, environment overrides, tests, and customer-specific
behavior start spreading across the codebase.
""".strip(),
}


def main() -> int:
    try:
        response = requests.post(API_URL, json=PAYLOAD, timeout=120)
    except requests.RequestException as exc:
        print(f"Request failed: {exc}")
        print("Make sure the API is running with: uvicorn app.main:app --reload")
        return 1

    print(f"Status: {response.status_code}")
    try:
        print(json.dumps(response.json(), indent=2))
    except ValueError:
        print(response.text)

    if response.status_code >= 400:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
