from enum import Enum, auto
import re
from zxcvbn import zxcvbn


class PasswordValidationError(Enum):
    MISSING = auto()
    MIN_LENGTH = auto()
    MAX_LENGTH = auto()
    MISMATCH = auto()
    REQUIRE_LETTER = auto()
    REQUIRE_DIGIT = auto()
    WEAK = auto()
    DISALLOWED_MATCH = auto()


def validate_password(
    password: str | None,
    *,
    confirm: str | None = None,
    require_confirm: bool = False,
    min_length: int | None = None,
    max_length: int | None = None,
    require_letter: bool = False,
    require_digit: bool = False,
    min_strength: int | None = None,
    disallow_current_password: str | None = None,
) -> PasswordValidationError | None:
    """Validate password input according to configurable requirements."""

    pw = password or ""
    if not pw:
        return PasswordValidationError.MISSING

    if max_length is not None and len(pw) > max_length:
        return PasswordValidationError.MAX_LENGTH

    if min_length is not None and len(pw) < min_length:
        return PasswordValidationError.MIN_LENGTH

    if require_letter and not re.search(r"[A-Za-z]", pw):
        return PasswordValidationError.REQUIRE_LETTER

    if require_digit and not re.search(r"\d", pw):
        return PasswordValidationError.REQUIRE_DIGIT

    if disallow_current_password is not None and pw == disallow_current_password:
        return PasswordValidationError.DISALLOWED_MATCH

    if require_confirm and not (confirm or ""):
        return PasswordValidationError.MISSING

    if confirm is not None and pw != (confirm or ""):
        return PasswordValidationError.MISMATCH

    if min_strength is not None:
        strength = zxcvbn(pw)
        if strength.get("score", 0) < min_strength:
            return PasswordValidationError.WEAK

    return None
