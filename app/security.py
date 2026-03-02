from __future__ import annotations

import base64
import hmac
from typing import Optional, Tuple


def _constant_time_eq(a: str, b: str) -> bool:
    # Avoid timing leaks
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def parse_basic_auth(authorization: Optional[str]) -> Optional[Tuple[str, str]]:
    """
    Returns (username, password) if Authorization header contains Basic auth,
    else None.
    """
    if not authorization:
        return None

    try:
        scheme, token = authorization.split(" ", 1)
    except ValueError:
        return None

    if scheme.lower() != "basic":
        return None

    try:
        raw = base64.b64decode(token.strip()).decode("utf-8")
        if ":" not in raw:
            return None
        user, pwd = raw.split(":", 1)
        return user, pwd
    except Exception:
        return None


def check_basic_auth(
    authorization: Optional[str],
    expected_user: str,
    expected_password: str,
) -> bool:
    creds = parse_basic_auth(authorization)
    if creds is None:
        return False
    user, pwd = creds
    return _constant_time_eq(user, expected_user) and _constant_time_eq(pwd, expected_password)
