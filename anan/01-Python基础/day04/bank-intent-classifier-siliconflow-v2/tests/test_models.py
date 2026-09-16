import pytest
from pydantic import ValidationError

from models import IntentResult, MultiIntentResult


def test_valid_intent_result():
    result = IntentResult(
        intent="transfer_remittance",
        confidence=0.97,
        need_human=False,
        summary="用户咨询跨行转账手续费。",
    )
    assert result.intent == "transfer_remittance"
    assert result.confidence == 0.97


def test_invalid_intent_rejected():
    with pytest.raises(ValidationError):
        IntentResult(
            intent="unknown_intent",
            confidence=0.8,
            need_human=False,
            summary="测试",
        )


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        IntentResult(
            intent="account_inquiry",
            confidence=1.2,
            need_human=False,
            summary="测试",
        )


def test_multi_intent_deduplicate():
    result = MultiIntentResult(
        intent="account_inquiry",
        confidence=0.9,
        need_human=False,
        summary="同时查询账户和信用卡账单。",
        all_intents=[
            "account_inquiry",
            "credit_card_service",
            "account_inquiry",
        ],
    )
    assert result.all_intents == ["account_inquiry", "credit_card_service"]
