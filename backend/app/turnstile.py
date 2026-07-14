import logging

import requests

from app.config import settings

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile_token(token: str, remote_ip: str | None = None) -> bool:
    if not token:
        return False

    payload = {"secret": settings.turnstile_secret_key, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        resp = requests.post(TURNSTILE_VERIFY_URL, data=payload, timeout=5)
        resp.raise_for_status()
        result = resp.json()
    except requests.RequestException:
        logger.exception("Turnstile verification request failed")
        return False

    if not result.get("success"):
        logger.info("Turnstile verification failed: %s", result.get("error-codes"))
    return bool(result.get("success"))
