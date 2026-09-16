"""命令行入口：对单条银行客服消息进行意图识别。"""

from __future__ import annotations

import argparse
import json
import sys

from classifier import BankIntentClassifier, ClassifierError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="基于 SiliconFlow DeepSeek 的银行客服五分类意图识别"
    )
    parser.add_argument("text", help="客户咨询消息，例如：我想查一下工资卡余额")
    parser.add_argument(
        "--multi",
        action="store_true",
        help="启用挑战项：识别一句话中的多个意图",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="输出单行紧凑 JSON",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        classifier = BankIntentClassifier()
        result = (
            classifier.classify_multi(args.text)
            if args.multi
            else classifier.classify(args.text)
        )
        payload = result.model_dump()
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=None if args.compact else 2,
            )
        )
        return 0
    except (ValueError, ClassifierError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
