"""SiliconFlow + DeepSeek 银行客服意图分类器。"""

from __future__ import annotations

import json
import time
from typing import Any, TypeVar

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from config import Settings, get_settings
from models import IntentResult, MultiIntentResult
from prompts import MULTI_INTENT_SYSTEM_PROMPT, SYSTEM_PROMPT


class ClassifierError(RuntimeError):
    """分类器统一异常基类。"""


class ConfigurationError(ClassifierError):
    pass


class ResponseFormatError(ClassifierError):
    pass


T = TypeVar("T", bound=BaseModel)


class BankIntentClassifier:
    """通过 SiliconFlow OpenAI 兼容接口调用 DeepSeek。"""

    def __init__(self, settings: Settings | None = None, client: OpenAI | None = None):
        self.settings = settings or get_settings()

        if client is None and not self.settings.api_key:
            raise ConfigurationError(
                "未检测到 SILICONFLOW_API_KEY。请复制 .env.example 为 .env 并填写 API Key。"
            )

        self.client = client or OpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_seconds,
            # 为了让作业中的重试逻辑可见，这里关闭 SDK 内置重试。
            max_retries=0,
        )

    def classify(self, text: str) -> IntentResult:
        """单意图分类：返回课程要求的四字段结果。"""
        text = self._validate_text(text)
        return self._request_and_validate(
            text=text,
            system_prompt=SYSTEM_PROMPT,
            model_cls=IntentResult,
        )

    def classify_multi(self, text: str) -> MultiIntentResult:
        """挑战项：识别单条消息中的多个银行业务意图。"""
        text = self._validate_text(text)
        result = self._request_and_validate(
            text=text,
            system_prompt=MULTI_INTENT_SYSTEM_PROMPT,
            model_cls=MultiIntentResult,
        )
        if result.intent not in result.all_intents:
            result.all_intents.insert(0, result.intent)
        return result

    @staticmethod
    def _validate_text(text: str) -> str:
        if not isinstance(text, str):
            raise ValueError("客户消息必须是字符串")
        text = text.strip()
        if not text:
            raise ValueError("客户消息不能为空")
        if len(text) > 4000:
            raise ValueError("客户消息过长，请控制在 4000 字符以内")
        return text

    def _request_and_validate(
        self,
        text: str,
        system_prompt: str,
        model_cls: type[T],
    ) -> T:
        """调用 API，并处理网络、限流、服务端错误和 JSON 校验错误。"""
        attempts = self.settings.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.settings.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    max_tokens=512,
                    stream=False,
                )

                content = response.choices[0].message.content
                if not content or not content.strip():
                    raise ResponseFormatError("模型返回了空内容")

                return self._parse_and_validate(content, model_cls)

            except AuthenticationError as exc:
                raise ConfigurationError(
                    "SiliconFlow API Key 无效或没有权限，请检查 SILICONFLOW_API_KEY。"
                ) from exc

            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                last_error = exc
                if attempt >= attempts - 1:
                    break
                self._sleep_before_retry(attempt)

            except APIStatusError as exc:
                last_error = exc
                # 5xx 通常可重试；其他状态码直接返回明确错误。
                if exc.status_code >= 500 and attempt < attempts - 1:
                    self._sleep_before_retry(attempt)
                    continue
                raise ClassifierError(
                    f"SiliconFlow API 请求失败，HTTP {exc.status_code}: {exc.message}"
                ) from exc

            except ResponseFormatError as exc:
                # JSON 输出偶发异常时允许重试，体现健壮性。
                last_error = exc
                if attempt >= attempts - 1:
                    break
                self._sleep_before_retry(attempt, short=True)

        raise ClassifierError(
            f"调用失败，已尝试 {attempts} 次。最后错误：{last_error}"
        ) from last_error

    @staticmethod
    def _sleep_before_retry(attempt: int, short: bool = False) -> None:
        # 1, 2, 4... 秒指数退避；格式问题用更短等待。
        base = 0.25 if short else 1.0
        time.sleep(base * (2**attempt))

    @staticmethod
    def _parse_and_validate(content: str, model_cls: type[T]) -> T:
        """解析 JSON 并用 Pydantic 做字段、类型、枚举、范围校验。"""
        raw = content.strip()

        # JSON Mode 正常不会带代码围栏；这里仅作为兼容性兜底。
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
            if raw.lower().startswith("json"):
                raw = raw[4:].lstrip()

        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ResponseFormatError(f"模型返回内容不是合法 JSON：{exc}") from exc

        if not isinstance(data, dict):
            raise ResponseFormatError("模型返回 JSON 必须是对象，而不是数组或其他类型")

        try:
            return model_cls.model_validate(data)
        except ValidationError as exc:
            raise ResponseFormatError(f"模型返回字段校验失败：{exc}") from exc
