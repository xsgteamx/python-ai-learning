"""Pydantic 数据模型，用于约束大模型输出格式。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Intent = Literal[
    "account_inquiry",
    "transfer_remittance",
    "credit_card_service",
    "loan_inquiry",
    "investment_wealth",
]

VALID_INTENTS: tuple[str, ...] = (
    "account_inquiry",
    "transfer_remittance",
    "credit_card_service",
    "loan_inquiry",
    "investment_wealth",
)

INTENT_NAMES: dict[str, str] = {
    "account_inquiry": "账户查询类",
    "transfer_remittance": "转账汇款类",
    "credit_card_service": "信用卡服务类",
    "loan_inquiry": "贷款咨询类",
    "investment_wealth": "投资理财类",
}

# 最新作业要求的最终 CSV 列名与顺序。
CSV_FIELDNAMES: tuple[str, str, str] = ("输入", "输出", "置信度分数")


class IntentResult(BaseModel):
    """课程要求的四字段结构化输出。"""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    need_human: bool
    summary: str = Field(min_length=1, max_length=200)

    @field_validator("summary")
    @classmethod
    def clean_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("summary 不能为空")
        return value


class MultiIntentResult(IntentResult):
    """挑战项：多意图模式，在四字段基础上增加 all_intents。"""

    all_intents: list[Intent] = Field(min_length=1)

    @field_validator("all_intents")
    @classmethod
    def deduplicate_intents(cls, value: list[Intent]) -> list[Intent]:
        # 保留原顺序去重
        return list(dict.fromkeys(value))
