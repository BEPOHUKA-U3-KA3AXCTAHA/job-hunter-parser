from __future__ import annotations

import pytest

from src.shared import Email


def test_valid_email() -> None:
    e = Email("alex@blockflow.ai")
    assert e.value == "alex@blockflow.ai"
    assert e.domain == "blockflow.ai"


def test_invalid_email_raises() -> None:
    with pytest.raises(ValueError):
        Email("not-an-email")


def test_email_with_plus() -> None:
    e = Email("user+tag@gmail.com")
    assert e.domain == "gmail.com"
