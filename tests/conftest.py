"""Keep the test suite independent from a developer's local .env file."""

import os


# Explicit constructor values used by execution tests still have higher
# Pydantic priority. These defaults prevent local credentials or execution
# flags from activating integration behavior in unrelated unit tests.
os.environ.update(
    {
        "ENV": "development",
        "ORDER_SUBMISSION_ENABLED": "false",
        "ACCOUNT_READ_ONLY": "true",
        "SHADOW_MODE": "true",
        "RUN_EXECUTION_SMOKE_TEST": "false",
        "TELEGRAM_ENABLED": "false",
    }
)
