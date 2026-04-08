import re


def validate_email(email: str) -> bool:
    """Validate whether a given string is a well-formed email address.

    The validation checks that the email contains a valid local part
    (alphanumeric characters, dots, underscores, percent signs, plus signs,
    or hyphens), exactly one '@' symbol, a domain part (alphanumeric
    characters, dots, or hyphens), and a top-level domain of at least
    two alphabetic characters.

    Args:
        email: The string to validate as an email address.

    Returns:
        True if the string is a valid email address, False otherwise.
    """
    pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}"
    return bool(re.fullmatch(pattern, email))
