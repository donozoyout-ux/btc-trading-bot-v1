"""Security module providing secret masking, centralized logging sanitation, and credential validation."""

import re
import sys
from typing import Optional, Tuple, Set, List
from loguru import logger


class SecurityManager:
    """
    Guarantees secrets isolation, centralized log sanitization, and environment validation.
    Prevents API keys or sensitive credentials from leaking into logs, databases, or outputs.
    """

    KNOWN_DUMMY_PATTERNS: Set[str] = {
        "your_binance_api_key_here",
        "your_binance_api_secret_here",
        "your_coinglass_api_key_here",
        "your_coinmarketcap_api_key_here",
        "test",
        "dummy",
        "123456",
    }

    # Sensitive key patterns to redact from query strings or headers
    SENSITIVE_KEY_PATTERNS = [
        re.compile(r"(signature=)([a-zA-Z0-9]+)", re.IGNORECASE),
        re.compile(r"(api[-_]?key['\":=\s]+)([a-zA-Z0-9_\-]+)", re.IGNORECASE),
        re.compile(r"(api[-_]?secret['\":=\s]+)([a-zA-Z0-9_\-]+)", re.IGNORECASE),
        re.compile(r"(X-MBX-APIKEY['\":=\s]+)([a-zA-Z0-9_\-]+)", re.IGNORECASE),
        re.compile(r"(CG-API-KEY['\":=\s]+)([a-zA-Z0-9_\-]+)", re.IGNORECASE),
        re.compile(r"(X-CMC_PRO_API_KEY['\":=\s]+)([a-zA-Z0-9_\-]+)", re.IGNORECASE),
        re.compile(r"(Authorization['\":=\s]+Bearer\s+)([a-zA-Z0-9_\-\.]+)", re.IGNORECASE),
    ]

    _registered_secrets: Set[str] = set()
    _is_log_sanitizer_installed: bool = False

    @classmethod
    def register_secret(cls, secret: Optional[str]) -> None:
        """Registers a known sensitive secret to be redacted across all logging channels."""
        if secret and len(secret.strip()) >= 5:
            cls._registered_secrets.add(secret.strip())

    @classmethod
    def mask_secret(cls, secret: Optional[str]) -> str:
        """
        Masks a secret string, leaving only first 3 and last 3 characters visible.
        Example: 'ABCD1234EFGH5678' -> 'ABC***678'
        """
        if not secret:
            return "<EMPTY>"
        clean = secret.strip()
        if len(clean) <= 6:
            return "***"
        return f"{clean[:3]}***{clean[-3:]}"

    @classmethod
    def validate_credentials(
        cls,
        env: str,
        api_key: Optional[str],
        api_secret: Optional[str],
        is_testnet: bool,
    ) -> Tuple[bool, str]:
        """
        Validates credentials for safety:
        - In production, dummy or placeholder keys are strictly rejected.
        - In production, testnet flag must NOT be True.
        - In testnet, production credentials must not be mixed.
        """
        env_lower = env.lower()
        key_clean = (api_key or "").strip().lower()
        secret_clean = (api_secret or "").strip().lower()

        if env_lower == "production":
            if is_testnet:
                return False, "Production environment cannot run with testnet flag enabled"
            if not api_key or not api_secret:
                return False, "Production environment requires non-empty API key and secret"
            if key_clean in cls.KNOWN_DUMMY_PATTERNS or secret_clean in cls.KNOWN_DUMMY_PATTERNS:
                return False, "Production environment contains dummy/placeholder credentials"
            if len(key_clean) < 16 or len(secret_clean) < 16:
                return False, "API key or secret length is suspiciously short for Binance Futures"

        elif env_lower == "testnet":
            if not is_testnet:
                return False, "Testnet environment must have BINANCE_TESTNET=True"

        return True, "Credentials validation passed"

    @classmethod
    def sanitize_string(cls, text: str) -> str:
        """Applies both pattern-based and registered secret redactions to any text."""
        sanitized = text

        # Redact registered secret values
        for sec in cls._registered_secrets:
            if sec in sanitized:
                sanitized = sanitized.replace(sec, cls.mask_secret(sec))

        # Redact common regex patterns (API keys, query signatures, auth headers)
        for pat in cls.SENSITIVE_KEY_PATTERNS:
            sanitized = pat.sub(r"\1***", sanitized)

        return sanitized

    @classmethod
    def setup_central_log_sanitizer(cls) -> None:
        """Installs a centralized loguru patcher to sanitize all log records."""
        if cls._is_log_sanitizer_installed:
            return

        def _sanitizing_patcher(record):
            record["message"] = cls.sanitize_string(record["message"])

        logger.configure(patcher=_sanitizing_patcher)
        cls._is_log_sanitizer_installed = True


# Initialize central sanitizer upon import
SecurityManager.setup_central_log_sanitizer()
