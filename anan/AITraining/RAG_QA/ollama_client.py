"""通过懒猫微服 Ollama 的原生 HTTP API 完成真实 Embedding 和问答。"""

import re
import requests


class OllamaError(RuntimeError):
    """Ollama 通信失败或生成结果不可用。"""


def extract_final_answer(content: str) -> str:
    """从 Qwen3 输出中保留最终答案，绝不将 <think> 误当答案。

    部分 Qwen3 / Ollama 组合会把思考内容放到 message.content，
    有时会缺失 <think> 开始标签，但保留 </think> 结束标签。
    """
    text = (content or "").strip()
    if re.search(r"</think\s*>", text, flags=re.I):
        text = re.split(r"</think\s*>", text, flags=re.I)[-1].strip()
    elif re.search(r"<think\s*>", text, flags=re.I):
        raise OllamaError("模型返回了未闭合的思考内容，没有可靠的最终答案")
    if not text:
        raise OllamaError("模型返回的最终答案为空")
    return text


class OllamaClient:
    def __init__(self, config):
        self.config = config
        self.session = requests.Session()

    def _post(self, endpoint, payload, timeout=300):
        try:
            response = self.session.post(
                self.config.ollama_url + endpoint,
                json=payload,
                timeout=(10, timeout),
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            raise OllamaError(
                f"Ollama 请求失败：{exc}。检查微服服务状态和 OLLAMA_URL"
            ) from exc
        except ValueError as exc:
            raise OllamaError(f"Ollama 返回的不是有效 JSON：{exc}") from exc

    def list_models(self):
        try:
            response = self.session.get(
                self.config.ollama_url + "/api/tags", timeout=(5, 20)
            )
            response.raise_for_status()
            return [item["name"] for item in response.json().get("models", [])]
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise OllamaError(f"无法读取模型列表：{exc}") from exc

    def embed(self, texts):
        if not texts:
            return []
        data = self._post(
            "/api/embed", {"model": self.config.embed_model, "input": texts}
        )
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise OllamaError("Embedding 接口返回向量数量与输入数量不匹配")
        if any(not isinstance(v, list) or len(v) != 1024 for v in vectors):
            raise OllamaError("BGE-M3 应输出 1024 维向量，请检查模型配置")
        return vectors

    def chat(self, system_prompt, user_prompt):
        """先关闭思考生成最终答案，必要时仅重试一次。

        不再像上一版那样强制 think=True：小型 Qwen3 在生成上限内
        可能只填充 message.thinking，使 message.content 为空。
        """
        initial_limit = max(256, int(self.config.num_predict))
        limits = [initial_limit, max(initial_limit * 2, 3072)]
        attempts = []

        for attempt_index, token_limit in enumerate(limits, start=1):
            payload = {
                "model": self.config.chat_model,
                "stream": False,
                "think": False,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "/no_think\n" + user_prompt},
                ],
                "options": {
                    "temperature": self.config.temperature,
                    "num_predict": token_limit,
                },
            }
            data = self._post("/api/chat", payload, timeout=600)
            message = data.get("message") or {}
            raw_content = message.get("content") or ""
            thinking = message.get("thinking") or ""
            done_reason = data.get("done_reason") or "unknown"
            eval_count = data.get("eval_count", 0)
            attempts.append(
                f"第{attempt_index}次: done_reason={done_reason}, "
                f"eval_count={eval_count}, content长度={len(raw_content)}, "
                f"thinking长度={len(thinking)}, num_predict={token_limit}"
            )

            try:
                answer = extract_final_answer(raw_content)
            except OllamaError:
                if attempt_index < len(limits):
                    continue
                raise OllamaError(
                    "模型连续两次没有返回可用的最终答案。"
                    + "；".join(attempts)
                    + "。可尝试切换非思考指令模型或查看微服 Ollama 日志。"
                ) from None

            # 如果输出因 Token 限制被切断，第二次增加额度重新生成，
            # 避免把不完整答案作为成功结果提交。
            if done_reason == "length":
                if attempt_index < len(limits):
                    continue
                raise OllamaError(
                    "模型连续两次达到生成长度上限，答案可能不完整。"
                    + "；".join(attempts)
                )

            return {
                "answer": answer,
                "model": data.get("model", self.config.chat_model),
                "done_reason": done_reason,
                "eval_count": eval_count,
                "thinking_length": len(thinking),
                "generation_attempts": attempt_index,
            }

        raise OllamaError("模型生成失败，未获取可用结果")
