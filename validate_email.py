import re


def validate_email(email: str) -> bool:
    """Validate whether a given string is a well-formed email address.

    Uses a regular expression to check that the email conforms to common
    formatting rules. The input string is **not** stripped or modified in
    any way before validation.

    Args:
        email: The string to validate as an email address.

    Returns:
        True if ``email`` matches the expected email format, False otherwise.

    Examples:
        >>> validate_email('user@example.com')
        True
        >>> validate_email('plainaddress')
        False
    """
    # Regex breakdown:
    #   Local part  : [a-zA-Z0-9]              – must start with an alphanumeric
    #                 ([a-zA-Z0-9._%+-]*       – followed by zero or more allowed chars
    #                  [a-zA-Z0-9])?           – if there are extra chars, must end with alphanumeric
    #                                            (the whole group is optional so a single-char local part works)
    #   @           : exactly one @ symbol
    #   Domain      : ([a-zA-Z0-9]             – each label starts with alphanumeric
    #                  ([a-zA-Z0-9-]*           – may contain hyphens in between
    #                   [a-zA-Z0-9])?           – if longer than one char, must end with alphanumeric
    #                  \.)+                     – followed by a dot; one or more such labels
    #   TLD         : [a-zA-Z]{2,}             – at least 2 alphabetic characters
    pattern = r'[a-zA-Z0-9]([a-zA-Z0-9._%+\-]*[a-zA-Z0-9])?@([a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}'

    return re.fullmatch(pattern, email) is not None
