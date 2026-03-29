"""Secrets manager — all credentials from environment variables only.

Never hardcode secrets. Never log secret values — log only the key name.
Raises SecretsError at startup if any required secret is missing.

Usage:
    secrets = SecretsManager()
    secrets.validate_required()   # call once at startup — raises on missing
    key = secrets.get_anthropic_key()
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Required secrets — missing any of these raises SecretsError at startup
_REQUIRED_SECRETS = (
    "ANTHROPIC_API_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
)


class SecretsError(RuntimeError):
    """Raised when required secrets are missing at startup."""


class SecretsManager:
    """Typed accessors for all application secrets loaded from environment variables.

    Call validate_required() at application startup to fail fast on missing secrets.
    """

    def validate_required(self) -> None:
        """Raise SecretsError if any required secret is not set.

        Never logs secret values — logs only the key names.
        """
        missing = []
        for key in _REQUIRED_SECRETS:
            value = os.environ.get(key)
            if not value:
                missing.append(key)
                logger.warning("SecretsManager: required secret '%s' is missing.", key)
            else:
                logger.debug("SecretsManager: '%s' found.", key)

        if missing:
            raise SecretsError(
                f"Application cannot start: the following required secrets are missing "
                f"from environment variables: {', '.join(missing)}. "
                f"See .env.example for required keys."
            )

        logger.info("SecretsManager: all %d required secrets present.", len(_REQUIRED_SECRETS))

    # ---- Typed accessors ----

    def get_anthropic_key(self) -> str:
        return self._require("ANTHROPIC_API_KEY")

    def get_database_url(self) -> str:
        return self._require("DATABASE_URL")

    def get_redis_url(self) -> str:
        return self._require("REDIS_URL")

    def get_neo4j_uri(self) -> str:
        return self._require("NEO4J_URI")

    def get_neo4j_credentials(self) -> tuple[str, str]:
        """Return (user, password) tuple."""
        return (self._require("NEO4J_USER"), self._require("NEO4J_PASSWORD"))

    def get_jira_credentials(self) -> Optional[dict]:
        """Return Jira credentials dict or None if not configured."""
        base_url = os.environ.get("JIRA_BASE_URL")
        token = os.environ.get("JIRA_API_TOKEN")
        email = os.environ.get("JIRA_USER_EMAIL")
        if not all((base_url, token, email)):
            logger.debug("SecretsManager: Jira credentials not fully configured.")
            return None
        return {"base_url": base_url, "api_token": token, "user_email": email}

    def get_github_token(self) -> Optional[str]:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            logger.debug("SecretsManager: GITHUB_TOKEN not set.")
        return token or None

    def get_slack_credentials(self) -> Optional[dict]:
        bot_token = os.environ.get("SLACK_BOT_TOKEN")
        signing_secret = os.environ.get("SLACK_SIGNING_SECRET")
        if not all((bot_token, signing_secret)):
            logger.debug("SecretsManager: Slack credentials not fully configured.")
            return None
        return {"bot_token": bot_token, "signing_secret": signing_secret}

    def get_smartsheet_token(self) -> Optional[str]:
        return os.environ.get("SMARTSHEET_ACCESS_TOKEN") or None

    # ---- Internal ----

    @staticmethod
    def _require(key: str) -> str:
        value = os.environ.get(key)
        if not value:
            raise SecretsError(
                f"Required secret '{key}' is not set. "
                f"Add it to your .env file or environment."
            )
        return value
