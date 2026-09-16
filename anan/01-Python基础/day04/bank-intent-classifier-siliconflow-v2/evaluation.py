"""批量读取课程 JSON 测试集，调用模型并导出作业要求的三列 CSV。"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from classifier import BankIntentClassifier, ClassifierError
from models import CSV_FIELDNAMES, INTENT_NAMES, VALID_INTENTS



DEFAULT_FILES = [
    "bank_intent_account_inquiry.json",
    "bank_intent_transfer_remittance.json",
    "bank_intent_credit_card_service.json",
    "bank_intent_loan_inquiry.json",
    "bank_intent_investment_wealth.json",
]


def load_dataset(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} 顶层必须是 JSON 数组")
    return data


def validate_dataset_item(item: dict[str, Any], source: Path) -> None:
    required = {"text", "intent", "intent_name", "id", "is_multi_intent"}
    missing = required - item.keys()
    if missing:
        raise ValueError(f"{source} 存在缺少字段的数据：{sorted(missing)}")
    if item["intent"] not in VALID_INTENTS:
        raise ValueError(f"{source} 存在未知 intent：{item['intent']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="批量识别银行客服五类意图，并导出作业规定格式的 CSV"
    )
    parser.add_argument("--data-dir", default="data", help="测试集目录")
    parser.add_argument(
        "--output",
        default="output/result.csv",
        help="CSV 输出路径，默认 output/result.csv",
    )
    parser.add_argument(
        "--limit-per-file",
        type=int,
        default=0,
        help="每个类别最多测试多少条；0 表示全部。建议先用 5 或 10 调试。",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="每个文件随机抽样（配合 --limit-per-file 使用）",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="每次 API 调用后的等待秒数，可用于降低限流风险",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    data_dir = Path(args.data_dir)
    output_path = Path(args.output)

    rng = random.Random(args.seed)
    samples: list[tuple[Path, dict[str, Any]]] = []

    try:
        for filename in DEFAULT_FILES:
            path = data_dir / filename
            if not path.exists():
                raise FileNotFoundError(f"未找到测试集：{path}")

            items = load_dataset(path)
            for item in items:
                validate_dataset_item(item, path)

            if args.shuffle:
                items = items.copy()
                rng.shuffle(items)

            if args.limit_per_file > 0:
                items = items[: args.limit_per_file]

            samples.extend((path, item) for item in items)

        if not samples:
            raise ValueError("没有可评估的数据")

        classifier = BankIntentClassifier()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # submission_rows 只保存老师要求提交的三列。
        # 评估统计仍在内存中计算，不把调试字段写进最终 CSV。
        submission_rows: list[dict[str, Any]] = []
        evaluation_rows: list[dict[str, Any]] = []
        class_stats = defaultdict(lambda: {"total": 0, "correct": 0, "errors": 0})

        for index, (source_path, item) in enumerate(samples, start=1):
            expected = item["intent"]
            print(f"[{index}/{len(samples)}] {item['text']}")

            predicted = ""
            confidence: float | str = ""
            need_human: bool | str = ""
            summary = ""
            error = ""
            correct = False

            try:
                result = classifier.classify(item["text"])
                predicted = result.intent
                confidence = result.confidence
                need_human = result.need_human
                summary = result.summary
                correct = predicted == expected
            except ClassifierError as exc:
                error = str(exc)

            class_stats[expected]["total"] += 1
            if correct:
                class_stats[expected]["correct"] += 1
            if error:
                class_stats[expected]["errors"] += 1

            # 最终作业 CSV 严格只有三列：输入、输出、置信度分数。
            # “输出”使用课程要求的中文类别名，而不是内部英文 intent。
            submission_rows.append(
                {
                    "输入": item["text"],
                    "输出": INTENT_NAMES.get(predicted, "") if predicted else "",
                    "置信度分数": confidence,
                }
            )

            # 下面这些字段只用于终端准确率统计，不写入提交 CSV。
            evaluation_rows.append(
                {
                    "source_file": source_path.name,
                    "id": item["id"],
                    "expected_intent": expected,
                    "predicted_intent": predicted,
                    "correct": correct,
                    "error": error,
                    "need_human": need_human,
                    "summary": summary,
                }
            )

            if args.delay > 0:
                time.sleep(args.delay)

        # utf-8-sig 对 Windows Excel 直接打开中文最稳妥，同时仍是标准 CSV。
        with output_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(submission_rows)

        total = len(evaluation_rows)
        correct_total = sum(bool(row["correct"]) for row in evaluation_rows)
        error_total = sum(bool(row["error"]) for row in evaluation_rows)
        accuracy = correct_total / total if total else 0.0

        print("\n=== 评估结果 ===")
        print(f"总样本数：{total}")
        print(f"正确数：{correct_total}")
        print(f"API/解析失败：{error_total}")
        print(f"总体准确率：{accuracy:.2%}")

        for intent in VALID_INTENTS:
            stats = class_stats[intent]
            if stats["total"] == 0:
                continue
            class_accuracy = stats["correct"] / stats["total"]
            print(
                f"{INTENT_NAMES[intent]} ({intent})："
                f"{stats['correct']}/{stats['total']} = {class_accuracy:.2%}"
            )

        print(f"CSV 已保存：{output_path}")
        return 0

    except (OSError, ValueError, json.JSONDecodeError, ClassifierError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
