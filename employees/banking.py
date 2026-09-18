"""Helpers for the bank details HR keeps on an employee.

Bank details reached the system through spreadsheets, where a leading zero is
lost the moment a cell is treated as a number: a 6-digit bank code such as
000014 arrives as "14", and an account number that starts with 0 arrives one
digit short. These helpers put back only what can be put back safely.
"""

import re

BANK_CODE_LENGTH = 6
ACCOUNT_LENGTH = 10


def normalize_bank_code(raw):
    """Restore the leading zeros of a bank (NIP) code: "14" -> "000014".

    Bank codes are a closed set of 6-digit codes, so left-padding an all-digit
    value is unambiguous. Anything else is returned trimmed and otherwise untouched.
    """
    value = (raw or "").strip()
    if value.isdigit() and len(value) < BANK_CODE_LENGTH:
        return value.zfill(BANK_CODE_LENGTH)
    return value


def clean_account_number(raw):
    """Drop spaces (including non-breaking ones) and hyphens people typed into an account number."""
    return re.sub(r"[\s \-]", "", str(raw or ""))


def to_account_number(raw):
    """Return (ten_digit_account, padded, problem).

    An 8-9 digit account is treated as one that lost its leading zeros and is
    padded to the 10-digit NUBAN - which is what the bank upload sheets have always
    done - but reported as `padded` so it can be double-checked, because a mistyped
    account can look the same. Nothing else is guessed: no account, non-digits, fewer
    than 8 or more than 10 digits come back as a problem instead.
    """
    account = clean_account_number(raw)
    if not account:
        return None, False, "No account number"
    if not account.isdigit():
        return None, False, f"Account number has non-digit characters ({account})"
    if len(account) > ACCOUNT_LENGTH:
        return None, False, f"Account number is {len(account)} digits (should be 10)"
    if len(account) < 8:
        return None, False, f"Account number is only {len(account)} digits (should be 10)"
    return account.zfill(ACCOUNT_LENGTH), len(account) < ACCOUNT_LENGTH, None


def to_bank_code(raw):
    """Return (six_digit_code, problem)."""
    code = normalize_bank_code(raw)
    if not code:
        return None, "No bank code"
    if not (code.isdigit() and len(code) == BANK_CODE_LENGTH):
        return None, f"Bank code {code!r} is not a 6-digit code"
    return code, None
