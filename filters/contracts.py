from config import (
    EXCLUDED_CONTRACT_SUFFIXES
)


def normalize_suffixes():

    return [
        str(suffix).strip().lower()
        for suffix in EXCLUDED_CONTRACT_SUFFIXES
        if str(suffix).strip()
    ]


def is_excluded_contract_address(address):

    normalized = str(address or "").strip().lower()

    if not normalized:
        return False

    return any(
        normalized.endswith(suffix)
        for suffix in normalize_suffixes()
    )
