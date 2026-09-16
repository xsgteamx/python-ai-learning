import csv
from io import StringIO

from models import CSV_FIELDNAMES, INTENT_NAMES


def test_submission_csv_headers_exact():
    assert list(CSV_FIELDNAMES) == ["输入", "输出", "置信度分数"]


def test_predicted_intent_maps_to_chinese_name():
    assert INTENT_NAMES["account_inquiry"] == "账户查询类"
    assert INTENT_NAMES["transfer_remittance"] == "转账汇款类"
    assert INTENT_NAMES["credit_card_service"] == "信用卡服务类"
    assert INTENT_NAMES["loan_inquiry"] == "贷款咨询类"
    assert INTENT_NAMES["investment_wealth"] == "投资理财类"


def test_csv_writer_produces_three_columns_only():
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDNAMES)
    writer.writeheader()
    writer.writerow(
        {
            "输入": "我想查一下工资卡余额",
            "输出": "账户查询类",
            "置信度分数": 0.95,
        }
    )
    rows = list(csv.DictReader(StringIO(output.getvalue())))
    assert list(rows[0].keys()) == list(CSV_FIELDNAMES)
    assert rows[0] == {
        "输入": "我想查一下工资卡余额",
        "输出": "账户查询类",
        "置信度分数": "0.95",
    }
