import pytest

from app.guardrails import (
    GuardrailError,
    allowed_offer_types,
    check_offer,
    check_status,
    money,
    truncate_note,
)

DISPUTE = {
    "status": "WAITING_FOR_SELLER_RESPONSE",
    "dispute_amount": {"currency_code": "USD", "value": "62.00"},
    "allowed_response_options": {"make_offer": {"offer_types": ["REFUND", "REFUND_WITH_RETURN"]}},
}


@pytest.mark.parametrize(
    "raw,expected",
    [("15.5", "15.50"), (15.5, "15.50"), ("$1,234.567", "1234.57"), (0, "0.00"), ("8", "8.00")],
)
def test_money_normalises_to_two_decimals(raw, expected):
    assert money(raw) == expected


def test_money_rejects_rubbish():
    with pytest.raises(GuardrailError):
        money("fifteen dollars")
    with pytest.raises(GuardrailError):
        money("-5.00")


def test_truncate_note_caps_at_2000_chars():
    assert len(truncate_note("x" * 5000)) == 2000
    assert truncate_note("  hi  ") == "hi"
    assert truncate_note(None) == ""


def test_offer_within_disputed_amount_is_accepted():
    assert check_offer(DISPUTE, "REFUND", "15.5") == ("REFUND", "15.50")


def test_offer_above_disputed_amount_is_blocked():
    with pytest.raises(GuardrailError, match="exceeds the disputed amount"):
        check_offer(DISPUTE, "REFUND", "62.01")


def test_offer_at_exactly_the_disputed_amount_is_allowed():
    assert check_offer(DISPUTE, "REFUND", "62.00")[1] == "62.00"


def test_disallowed_offer_type_is_blocked():
    with pytest.raises(GuardrailError, match="not allowed"):
        check_offer(DISPUTE, "REFUND_WITH_REPLACEMENT", "10.00")


def test_zero_offer_is_blocked():
    with pytest.raises(GuardrailError, match="greater than zero"):
        check_offer(DISPUTE, "REFUND", "0")


def test_offer_blocked_when_paypal_allows_none():
    dispute = {**DISPUTE, "allowed_response_options": {"participant_actions": ["ACCEPT_CLAIM"]}}
    assert allowed_offer_types(dispute) == []
    with pytest.raises(GuardrailError, match="does not allow an offer"):
        check_offer(dispute, "REFUND", "1.00")


def test_status_must_be_waiting_for_seller():
    check_status(DISPUTE)
    with pytest.raises(GuardrailError, match="WAITING_FOR_SELLER_RESPONSE"):
        check_status({**DISPUTE, "status": "RESOLVED"})
