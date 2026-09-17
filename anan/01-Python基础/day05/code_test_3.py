import os
import csv
import json
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# ==============================
# 基础配置
# ==============================

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PROMPT_FILE = BASE_DIR / "prompt.txt"
CSV_FILE = BASE_DIR / "result.csv"
DETAIL_FILE = BASE_DIR / "result_detail.json"
REPORT_FILE = BASE_DIR / "analyze.md"

API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

if not API_KEY:
    raise RuntimeError(
        "未读取到 DEEPSEEK_API_KEY，请在同目录 .env 中配置：\n"
        "DEEPSEEK_API_KEY=你的APIKey"
    )

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL
)

SYSTEM_PROMPT = PROMPT_FILE.read_text(encoding="utf-8")


# ==============================
# 20条测试集
# 覆盖：正常、边界、异常
# ==============================

TEST_CASES = [
    {
        "id": "T01",
        "scene": "普通低风险客户",
        "expected": "PASS",
        "input": {
            "applicant_id": "T01",
            "monthly_income": 15000,
            "employment_months": 36,
            "existing_monthly_debt_payment": 2000,
            "proposed_monthly_payment": 2500,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T02",
        "scene": "高收入低负债客户",
        "expected": "PASS",
        "input": {
            "applicant_id": "T02",
            "monthly_income": 30000,
            "employment_months": 72,
            "existing_monthly_debt_payment": 3000,
            "proposed_monthly_payment": 3000,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 1,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T03",
        "scene": "DTI恰好40%",
        "expected": "PASS",
        "input": {
            "applicant_id": "T03",
            "monthly_income": 10000,
            "employment_months": 24,
            "existing_monthly_debt_payment": 2000,
            "proposed_monthly_payment": 2000,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T04",
        "scene": "DTI略高于40%",
        "expected": "MANUAL_REVIEW",
        "input": {
            "applicant_id": "T04",
            "monthly_income": 10000,
            "employment_months": 24,
            "existing_monthly_debt_payment": 2000,
            "proposed_monthly_payment": 2100,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T05",
        "scene": "DTI恰好60%",
        "expected": "MANUAL_REVIEW",
        "input": {
            "applicant_id": "T05",
            "monthly_income": 10000,
            "employment_months": 24,
            "existing_monthly_debt_payment": 3000,
            "proposed_monthly_payment": 3000,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T06",
        "scene": "DTI高于60%",
        "expected": "REJECT",
        "input": {
            "applicant_id": "T06",
            "monthly_income": 10000,
            "employment_months": 24,
            "existing_monthly_debt_payment": 3200,
            "proposed_monthly_payment": 3000,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T07",
        "scene": "最近24个月逾期1次",
        "expected": "PASS",
        "input": {
            "applicant_id": "T07",
            "monthly_income": 15000,
            "employment_months": 36,
            "existing_monthly_debt_payment": 1500,
            "proposed_monthly_payment": 2500,
            "overdue_count_24m": 1,
            "max_overdue_days_24m": 10,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T08",
        "scene": "最近24个月逾期2次",
        "expected": "MANUAL_REVIEW",
        "input": {
            "applicant_id": "T08",
            "monthly_income": 15000,
            "employment_months": 36,
            "existing_monthly_debt_payment": 1500,
            "proposed_monthly_payment": 2500,
            "overdue_count_24m": 2,
            "max_overdue_days_24m": 15,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T09",
        "scene": "最近24个月逾期4次",
        "expected": "REJECT",
        "input": {
            "applicant_id": "T09",
            "monthly_income": 15000,
            "employment_months": 36,
            "existing_monthly_debt_payment": 1500,
            "proposed_monthly_payment": 2500,
            "overdue_count_24m": 4,
            "max_overdue_days_24m": 20,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T10",
        "scene": "最大逾期29天",
        "expected": "PASS",
        "input": {
            "applicant_id": "T10",
            "monthly_income": 16000,
            "employment_months": 36,
            "existing_monthly_debt_payment": 1500,
            "proposed_monthly_payment": 2500,
            "overdue_count_24m": 1,
            "max_overdue_days_24m": 29,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T11",
        "scene": "最大逾期30天",
        "expected": "MANUAL_REVIEW",
        "input": {
            "applicant_id": "T11",
            "monthly_income": 16000,
            "employment_months": 36,
            "existing_monthly_debt_payment": 1500,
            "proposed_monthly_payment": 2500,
            "overdue_count_24m": 1,
            "max_overdue_days_24m": 30,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T12",
        "scene": "最大逾期90天",
        "expected": "REJECT",
        "input": {
            "applicant_id": "T12",
            "monthly_income": 16000,
            "employment_months": 36,
            "existing_monthly_debt_payment": 1500,
            "proposed_monthly_payment": 2500,
            "overdue_count_24m": 1,
            "max_overdue_days_24m": 90,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T13",
        "scene": "近6个月征信查询5次",
        "expected": "PASS",
        "input": {
            "applicant_id": "T13",
            "monthly_income": 16000,
            "employment_months": 36,
            "existing_monthly_debt_payment": 1500,
            "proposed_monthly_payment": 2500,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 5,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T14",
        "scene": "近6个月征信查询6次",
        "expected": "MANUAL_REVIEW",
        "input": {
            "applicant_id": "T14",
            "monthly_income": 16000,
            "employment_months": 36,
            "existing_monthly_debt_payment": 1500,
            "proposed_monthly_payment": 2500,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 6,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T15",
        "scene": "当前未结清贷款3笔",
        "expected": "PASS",
        "input": {
            "applicant_id": "T15",
            "monthly_income": 18000,
            "employment_months": 48,
            "existing_monthly_debt_payment": 2500,
            "proposed_monthly_payment": 2500,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 3,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T16",
        "scene": "当前未结清贷款4笔",
        "expected": "MANUAL_REVIEW",
        "input": {
            "applicant_id": "T16",
            "monthly_income": 18000,
            "employment_months": 48,
            "existing_monthly_debt_payment": 2500,
            "proposed_monthly_payment": 2500,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 4,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T17",
        "scene": "就业稳定期11个月",
        "expected": "MANUAL_REVIEW",
        "input": {
            "applicant_id": "T17",
            "monthly_income": 15000,
            "employment_months": 11,
            "existing_monthly_debt_payment": 1500,
            "proposed_monthly_payment": 2500,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T18",
        "scene": "命中欺诈或黑名单",
        "expected": "REJECT",
        "input": {
            "applicant_id": "T18",
            "monthly_income": 30000,
            "employment_months": 72,
            "existing_monthly_debt_payment": 1000,
            "proposed_monthly_payment": 1000,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 1,
            "existing_loan_count": 0,
            "fraud_or_blacklist_hit": True
        }
    },
    {
        "id": "T19",
        "scene": "缺少月收入字段",
        "expected": "MANUAL_REVIEW",
        "input": {
            "applicant_id": "T19",
            "employment_months": 24,
            "existing_monthly_debt_payment": 1500,
            "proposed_monthly_payment": 2000,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    },
    {
        "id": "T20",
        "scene": "月收入为非法负数",
        "expected": "MANUAL_REVIEW",
        "input": {
            "applicant_id": "T20",
            "monthly_income": -1000,
            "employment_months": 24,
            "existing_monthly_debt_payment": 1500,
            "proposed_monthly_payment": 2000,
            "overdue_count_24m": 0,
            "max_overdue_days_24m": 0,
            "credit_inquiries_6m": 2,
            "existing_loan_count": 1,
            "fraud_or_blacklist_hit": False
        }
    }
]



# ==============================
# API连通性测试
# ==============================

def test_connection():
    """先用最小请求确认API Key、Base URL和模型是否可用。"""
    print("正在测试DeepSeek API连通性 ...")
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "user", "content": "只回复 OK"}
            ],
            temperature=0
        )
        content = response.choices[0].message.content
        print(f"API连通正常：{content}")
        print("-" * 60)
        return True
    except Exception as exc:
        print("API连通测试失败：")
        print(repr(exc))
        print()
        print("请优先检查：")
        print("1. DEEPSEEK_API_KEY 是否正确、是否有余额；")
        print("2. DEEPSEEK_BASE_URL 是否为 https://api.deepseek.com；")
        print("3. 当前网络是否能访问 DeepSeek API；")
        print("4. deepseek-chat 模型是否可用。")
        return False


# ==============================
# 结果校验
# ==============================

REQUIRED_FIELDS = {
    "applicant_id",
    "decision",
    "risk_level",
    "dti",
    "analysis_steps",
    "risk_factors",
    "missing_information",
    "manual_review_required",
    "summary",
}

VALID_DECISIONS = {"PASS", "MANUAL_REVIEW", "REJECT"}
VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH"}


def validate_result(data):
    errors = []

    if not isinstance(data, dict):
        return ["返回结果不是JSON对象"]

    missing = sorted(REQUIRED_FIELDS - set(data.keys()))
    if missing:
        errors.append(f"缺少字段: {', '.join(missing)}")

    extra = sorted(set(data.keys()) - REQUIRED_FIELDS)
    if extra:
        errors.append(f"存在未定义字段: {', '.join(extra)}")

    if data.get("decision") not in VALID_DECISIONS:
        errors.append("decision枚举值非法")

    if data.get("risk_level") not in VALID_RISK_LEVELS:
        errors.append("risk_level枚举值非法")

    steps = data.get("analysis_steps")
    if not isinstance(steps, list) or len(steps) != 5:
        errors.append("analysis_steps必须恰好包含5个步骤")

    if not isinstance(data.get("risk_factors"), list):
        errors.append("risk_factors必须为数组")

    if not isinstance(data.get("missing_information"), list):
        errors.append("missing_information必须为数组")

    if not isinstance(data.get("manual_review_required"), bool):
        errors.append("manual_review_required必须为布尔值")

    return errors


# ==============================
# 调用模型
# ==============================

def call_model(case):
    application_json = json.dumps(
        case["input"],
        ensure_ascii=False,
        indent=2
    )

    system_prompt = SYSTEM_PROMPT.replace(
        "{{loan_application}}",
        "申请数据将在下一条user消息中提供。"
    )

    user_prompt = (
        "请严格按照system中的全部规则，对以下个人消费信贷申请执行初审。\n"
        "最终只输出一个合法JSON对象。\n\n"
        + application_json
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0
    )

    return response.choices[0].message.content


def run_case(case):
    started = time.time()

    try:
        raw = call_model(case)
        parsed = json.loads(raw)

        validation_errors = validate_result(parsed)

        actual = parsed.get("decision")
        decision_correct = actual == case["expected"]
        schema_ok = len(validation_errors) == 0

        return {
            "id": case["id"],
            "scene": case["scene"],
            "expected": case["expected"],
            "actual": actual,
            "decision_correct": decision_correct,
            "schema_ok": schema_ok,
            "validation_errors": validation_errors,
            "elapsed_seconds": round(time.time() - started, 2),
            "input": case["input"],
            "output": parsed,
            "raw_output": raw,
            "error": None
        }

    except Exception as exc:
        return {
            "id": case["id"],
            "scene": case["scene"],
            "expected": case["expected"],
            "actual": None,
            "decision_correct": False,
            "schema_ok": False,
            "validation_errors": [],
            "elapsed_seconds": round(time.time() - started, 2),
            "input": case["input"],
            "output": None,
            "raw_output": None,
            "error": str(exc)
        }


# ==============================
# 导出结果
# ==============================

def save_csv(results):
    with CSV_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "scene",
                "expected",
                "actual",
                "decision_correct",
                "schema_ok",
                "elapsed_seconds",
                "validation_errors",
                "error"
            ]
        )
        writer.writeheader()

        for item in results:
            writer.writerow({
                "id": item["id"],
                "scene": item["scene"],
                "expected": item["expected"],
                "actual": item["actual"],
                "decision_correct": item["decision_correct"],
                "schema_ok": item["schema_ok"],
                "elapsed_seconds": item["elapsed_seconds"],
                "validation_errors": "; ".join(item["validation_errors"]),
                "error": item["error"] or ""
            })


def save_detail(results):
    DETAIL_FILE.write_text(
        json.dumps(
            results,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def generate_report(results):
    total = len(results)
    correct = sum(1 for x in results if x["decision_correct"])
    schema_ok = sum(1 for x in results if x["schema_ok"])
    accuracy = correct / total * 100 if total else 0
    schema_rate = schema_ok / total * 100 if total else 0

    lines = []

    lines.append("# 银行个人消费信贷智能辅助初审 Prompt 测试与问题分析报告")
    lines.append("")
    lines.append("## 1. 作业目标")
    lines.append("")
    lines.append(
        "本次实战围绕银行个人消费信贷初审场景，"
        "通过工程化Prompt设计实现对信贷申请数据的结构化辅助初审。"
        "Prompt采用元指令、行为约束、JSON Schema、Few-shot四层结构，"
        "并通过分步骤分析流程提升结果的可解释性、稳定性和可追溯性。"
    )
    lines.append("")

    lines.append("## 2. Prompt设计")
    lines.append("")
    lines.append("### 2.1 L1 元指令")
    lines.append("")
    lines.append(
        "将模型设定为银行个人消费信贷初审分析专家，"
        "明确其任务为信息完整性检查、DTI计算、风险识别和初审决策。"
    )
    lines.append("")
    lines.append("### 2.2 L2 行为约束")
    lines.append("")
    lines.append(
        "通过禁止虚构、缺失信息转人工复核、决策优先级和输出边界等规则，"
        "限制模型自由发挥。"
    )
    lines.append("")
    lines.append("### 2.3 L3 JSON Schema")
    lines.append("")
    lines.append(
        "对decision、risk_level、analysis_steps、risk_factors等字段进行类型、"
        "枚举、必填项和字段数量约束，确保结果可被程序直接解析。"
    )
    lines.append("")
    lines.append("### 2.4 L4 Few-shot")
    lines.append("")
    lines.append(
        "设计PASS、REJECT、MANUAL_REVIEW三类代表性示例，"
        "帮助模型学习不同决策分支的输入输出模式。"
    )
    lines.append("")
    lines.append("### 2.5 分步骤分析")
    lines.append("")
    lines.append(
        "按照信息检查、偿债能力、严重风险、其他风险、综合决策五个步骤执行，"
        "并在analysis_steps中保留关键业务依据。"
    )
    lines.append("")

    lines.append("## 3. 测试方法")
    lines.append("")
    lines.append(
        "共设计20条测试用例，覆盖正常场景、边界场景和异常场景。"
        "测试重点包括DTI边界、逾期次数、最大逾期天数、征信查询次数、"
        "贷款数量、就业稳定性、黑名单以及关键字段缺失等情况。"
    )
    lines.append("")
    lines.append(
        "每条测试均事先根据Prompt中的明确业务规则确定预期decision，"
        "再与模型实际输出进行比对，同时校验JSON字段结构。"
    )
    lines.append("")

    lines.append("## 4. 全量测试结果")
    lines.append("")
    lines.append("| ID | 测试场景 | 预期结果 | 实际结果 | 决策正确 | Schema校验 |")
    lines.append("|---|---|---|---|---|---|")

    for item in results:
        lines.append(
            f"| {item['id']} | {item['scene']} | "
            f"{item['expected']} | {item['actual'] or 'ERROR'} | "
            f"{'是' if item['decision_correct'] else '否'} | "
            f"{'通过' if item['schema_ok'] else '失败'} |"
        )

    lines.append("")
    lines.append("## 5. 准确率统计")
    lines.append("")
    lines.append(f"- 测试用例总数：{total}")
    lines.append(f"- 决策正确数：{correct}")
    lines.append(f"- 决策错误数：{total - correct}")
    lines.append(f"- 决策准确率：**{accuracy:.2f}%**")
    lines.append(f"- Schema校验通过数：{schema_ok}")
    lines.append(f"- Schema符合率：**{schema_rate:.2f}%**")
    lines.append("")

    bad_cases = [
        x for x in results
        if (not x["decision_correct"]) or (not x["schema_ok"]) or x["error"]
    ]

    lines.append("## 6. Bad Case与问题分析")
    lines.append("")

    if not bad_cases:
        lines.append(
            "本轮20条测试用例的决策结果与预期一致，"
            "且输出结构均通过字段校验，未发现明显Bad Case。"
        )
        lines.append("")
        lines.append(
            "后续仍可继续扩大测试集，增加多风险因素同时命中、"
            "极端数值、字段类型错误等更复杂场景进行压力测试。"
        )
    else:
        for index, item in enumerate(bad_cases, start=1):
            lines.append(f"### 6.{index} {item['id']} - {item['scene']}")
            lines.append("")
            lines.append(f"- 预期结果：{item['expected']}")
            lines.append(f"- 实际结果：{item['actual'] or '未获得有效结果'}")
            if item["validation_errors"]:
                lines.append(
                    "- Schema问题：" + "；".join(item["validation_errors"])
                )
            if item["error"]:
                lines.append("- 调用异常：" + item["error"])
            lines.append(
                "- 问题分析：需要结合该用例的实际输出，检查业务阈值、"
                "决策优先级或结构化输出约束是否存在歧义。"
            )
            lines.append(
                "- 优化方向：对出现歧义的规则进行进一步量化，"
                "必要时增加对应边界Few-shot示例后重新执行全量回归测试。"
            )
            lines.append("")

    lines.append("## 7. Prompt迭代思路")
    lines.append("")
    lines.append(
        "Prompt优化采用“编写 → 测试 → 分析Bad Case → 修改单一变量 → "
        "全量回归”的闭环方式。每次修改尽量只调整一个规则或约束，"
        "从而定位准确率变化的真实原因。"
    )
    lines.append("")
    lines.append(
        "对于边界判断错误，优先强化数值范围和等号边界；"
        "对于输出格式错误，优先强化JSON Schema、枚举值和必填字段；"
        "对于业务判断偏差，则通过补充具有代表性的Few-shot进行校准。"
    )
    lines.append("")

    lines.append("## 8. 结论")
    lines.append("")
    lines.append(
        f"本次共完成{total}条信贷初审测试，决策准确率为{accuracy:.2f}%，"
        f"Schema符合率为{schema_rate:.2f}%。"
        "测试结果表明，结构化Prompt能够较稳定地按照预设业务规则输出"
        "PASS、MANUAL_REVIEW和REJECT三类初审结果，"
        "同时通过analysis_steps保留关键判断依据，"
        "提高了结果的可解释性和程序可解析性。"
    )
    lines.append("")

    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")


# ==============================
# 主程序
# ==============================

def main():
    print(f"模型：{MODEL}")
    print(f"Base URL：{BASE_URL}")
    print(f"测试用例：{len(TEST_CASES)}条")
    print("-" * 60)

    if not test_connection():
        raise SystemExit(1)

    results = []

    for index, case in enumerate(TEST_CASES, start=1):
        print(
            f"[{index:02d}/{len(TEST_CASES)}] "
            f"{case['id']} {case['scene']} ... ",
            end="",
            flush=True
        )

        result = run_case(case)
        results.append(result)

        if result["error"]:
            print("ERROR")
            print(f"    异常：{result['error']}")
        elif result["decision_correct"] and result["schema_ok"]:
            print(f"PASS ({result['actual']})")
        else:
            print(
                f"CHECK "
                f"(expected={result['expected']}, actual={result['actual']})"
            )

        # 避免连续请求过快，可按接口限制调整
        time.sleep(0.3)

    save_csv(results)
    save_detail(results)
    generate_report(results)

    total = len(results)
    correct = sum(1 for x in results if x["decision_correct"])
    schema_ok = sum(1 for x in results if x["schema_ok"])

    print("-" * 60)
    print(f"决策准确率：{correct}/{total} = {correct / total * 100:.2f}%")
    print(f"Schema符合率：{schema_ok}/{total} = {schema_ok / total * 100:.2f}%")
    print(f"CSV结果：{CSV_FILE}")
    print(f"详细结果：{DETAIL_FILE}")
    print(f"Markdown报告：{REPORT_FILE}")


if __name__ == "__main__":
    main()