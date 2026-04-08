"""Email validation utility."""

import re


def validate_email(email: str) -> bool:
    """Validate an email address using regex.

    Args:
        email: The email string to validate.

    Returns:
        True if the email is valid, False otherwise.
    """
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))