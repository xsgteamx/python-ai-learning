import pytest

pytest.importorskip("openai")

from classifier import BankIntentClassifier, ResponseFormatError
from models import IntentResult


def test_parse_valid_json():
    content = '''{
      "intent": "loan_inquiry",
      "confidence": 0.93,
      "need_human": false,
      "summary": "用户咨询消费贷款申请条件。"
    }'''
    result = BankIntentClassifier._parse_and_validate(content, IntentResult)
    assert result.intent == "loan_inquiry"


def test_parse_invalid_json():
    with pytest.raises(ResponseFormatError):
        BankIntentClassifier._parse_and_validate("not json", IntentResult)


def test_parse_invalid_schema():
    content = '''{
      "intent": "loan_inquiry",
      "confidence": 2,
      "need_human": false,
      "summary": "测试"
    }'''
    with pytest.raises(ResponseFormatError):
        BankIntentClassifier._parse_and_validate(content, IntentResult)
