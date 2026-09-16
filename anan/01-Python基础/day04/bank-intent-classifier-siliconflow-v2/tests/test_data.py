import json
from pathlib import Path

from models import VALID_INTENTS


EXPECTED = {
    "bank_intent_account_inquiry.json": (500, "account_inquiry"),
    "bank_intent_transfer_remittance.json": (550, "transfer_remittance"),
    "bank_intent_credit_card_service.json": (600, "credit_card_service"),
    "bank_intent_loan_inquiry.json": (650, "loan_inquiry"),
    "bank_intent_investment_wealth.json": (700, "investment_wealth"),
}


def test_course_datasets_are_valid():
    data_dir = Path(__file__).resolve().parents[1] / "data"

    for filename, (expected_count, expected_intent) in EXPECTED.items():
        path = data_dir / filename
        with path.open("r", encoding="utf-8") as f:
            rows = json.load(f)

        assert len(rows) == expected_count
        assert all(row["intent"] == expected_intent for row in rows)
        assert expected_intent in VALID_INTENTS
        assert all(row["is_multi_intent"] is False for row in rows)
