"""Unit tests for core/security.py (Phase 0 Gate)."""

import pytest
from core.security import SecurityManager


def test_mask_secret():
    assert SecurityManager.mask_secret("BINANCE_API_KEY_123456789") == "BIN***789"
    assert SecurityManager.mask_secret("123456") == "***"
    assert SecurityManager.mask_secret("") == "<EMPTY>"
    assert SecurityManager.mask_secret(None) == "<EMPTY>"


def test_validate_credentials_production():
    # Production with dummy credentials must be rejected
    is_valid, msg = SecurityManager.validate_credentials(
        env="production",
        api_key="your_binance_api_key_here",
        api_secret="your_binance_api_secret_here",
        is_testnet=False,
    )
    assert not is_valid
    assert "placeholder" in msg

    # Production with testnet=True must be rejected
    is_valid, msg = SecurityManager.validate_credentials(
        env="production",
        api_key="REALKEY123456789012",
        api_secret="REALSECRET123456789012",
        is_testnet=True,
    )
    assert not is_valid
    assert "testnet flag" in msg

    # Production with valid credentials
    is_valid, msg = SecurityManager.validate_credentials(
        env="production",
        api_key="REALKEY123456789012",
        api_secret="REALSECRET123456789012",
        is_testnet=False,
    )
    assert is_valid


def test_validate_credentials_testnet():
    # Testnet without testnet=True must be rejected
    is_valid, msg = SecurityManager.validate_credentials(
        env="testnet",
        api_key="TESTKEY123456789012",
        api_secret="TESTSECRET123456789012",
        is_testnet=False,
    )
    assert not is_valid
    assert "BINANCE_TESTNET=True" in msg


def test_sanitize_string():
    secret_key = "TOP_SECRET_BINANCE_KEY_999"
    SecurityManager.register_secret(secret_key)
    raw_msg = f"Connecting to exchange using key={secret_key} now"
    sanitized = SecurityManager.sanitize_string(raw_msg)
    assert secret_key not in sanitized
    assert "TOP***999" in sanitized
