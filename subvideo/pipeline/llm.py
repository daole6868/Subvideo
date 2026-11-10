"""LLM providers for translation.

* anthropic  - official Anthropic SDK (Claude), structured JSON output
* gemini     - google-genai SDK
* openai / deepseek / openrouter / ollama / custom - OpenAI SDK against any
  OpenAI-compatible endpoint (local models through Ollama, LM Studio, vLLM...)
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from ..config import Settings

PROVIDERS = [
    ("gemini", "Google Gemini"),
    ("anthropic", "Anthropic Claude"),
    ("openai", "OpenAI"),
    ("deepseek", "DeepSeek"),
    ("openrouter", "OpenRouter"),
    ("ollama", "Ollama (máy local)"),
    ("custom", "Máy chủ tương thích OpenAI (LM Studio, vLLM…)"),
]
OPENAI_COMPAT = {"openai", "deepseek", "openrouter", "ollama", "custom"}
# Claude models that accept the server-side refusal fallback parameter
_CLAUDE_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")


class LLMError(RuntimeError):
    pass


def parse_json(text: str) -> Any:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError("Mô hình không trả về JSON hợp lệ:\n" + text[:500])


class LLMClient:
    def __init__(self, provider: str | None = None, model: str | None = None):
        s = Settings.get()
        self.provider = provider or s["translate_provider"]
        self.cfg = dict(s.provider(self.provider))
        if model:
            self.cfg["model"] = model
        self.model = self.cfg.get("model", "")
        if not self.model:
            raise LLMError(f"Chưa chọn model cho {self.provider} (Cài đặt → Dịch).")
        self._client = None

    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    # --------------------------------------------------------------- clients
    def _anthropic(self):
        import anthropic

        if self._client is None:
            key = self.cfg.get("api_key") or None
            self._client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=600.0) if key else anthropic.Anthropic(max_retries=3, timeout=600.0)
        return self._client

    def _openai(self):
        from openai import OpenAI

        if self._client is None:
            base = self.cfg.get("base_url") or None
            key = self.cfg.get("api_key") or ("ollama" if self.provider == "ollama" else None)
            self._client = OpenAI(api_key=key, base_url=base, timeout=600, max_retries=3)
        return self._client

    def _gemini(self):
        from google import genai
        from google.genai import types

        if self._client is None:
            key = self.cfg.get("api_key") or None
            self._client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=600_000))
        return self._client

    # ------------------------------------------------------------- requests
    def complete_json(self, system: str, user: str, schema: dict | None = None, max_tokens: int = 32000,
                      attempts: int = 2) -> Any:
        last: Exception | None = None
        for i in range(attempts):
            try:
                text = self._complete(system, user, schema, max_tokens)
                return parse_json(text)
            except LLMError as e:
                last = e
                time.sleep(1.5 * (i + 1))
        raise last or LLMError("Lỗi không xác định")

    def _complete(self, system: str, user: str, schema: dict | None, max_tokens: int) -> str:
        if self.provider == "anthropic":
            return self._complete_anthropic(system, user, schema, max_tokens)
        if self.provider == "gemini":
            return self._complete_gemini(system, user)
        return self._complete_openai(system, user)

    def _complete_anthropic(self, system: str, user: str, schema: dict | None, max_tokens: int) -> str:
        import anthropic

        client = self._anthropic()
        output_config: dict[str, Any] = {"effort": self.cfg.get("effort") or "medium"}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            # The project "bible" is stable for a whole episode -> cache it.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
        try:
            if self.model.startswith(_CLAUDE_FALLBACK_MODELS):
                try:
                    with client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                                     **kwargs) as stream:
                        msg = stream.get_final_message()
                except TypeError:  # older SDK without the fallbacks parameter
                    with client.messages.stream(**kwargs) as stream:
                        msg = stream.get_final_message()
            else:
                with client.messages.stream(**kwargs) as stream:
                    msg = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise LLMError("Khoá API Anthropic không hợp lệ.") from e
        except anthropic.BadRequestError as e:
            raise LLMError(f"Yêu cầu không hợp lệ: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Lỗi Anthropic API ({e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Không kết nối được Anthropic API.") from e
        if msg.stop_reason == "refusal":
            raise LLMError("Claude từ chối yêu cầu này (refusal).")
        if msg.stop_reason == "max_tokens":
            raise LLMError("Câu trả lời bị cắt (max_tokens) — hãy giảm số câu mỗi lần dịch.")
        texts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
        if not texts:
            raise LLMError("Claude không trả về nội dung.")
        return texts[0]

    def _complete_openai(self, system: str, user: str) -> str:
        from openai import APIConnectionError, APIStatusError, AuthenticationError, BadRequestError

        client = self._openai()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            try:
                r = client.chat.completions.create(model=self.model, messages=messages,
                                                   response_format={"type": "json_object"})
            except BadRequestError:
                r = client.chat.completions.create(model=self.model, messages=messages)
        except AuthenticationError as e:
            raise LLMError(f"Khoá API {self.provider} không hợp lệ.") from e
        except APIConnectionError as e:
            raise LLMError(f"Không kết nối được {self.provider} ({self.cfg.get('base_url') or 'mặc định'}).") from e
        except APIStatusError as e:
            raise LLMError(f"Lỗi {self.provider} ({e.status_code}): {e.message}") from e
        content = r.choices[0].message.content or ""
        # some reasoning models wrap thoughts in <think> tags
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
        if r.choices[0].finish_reason == "length":
            raise LLMError("Câu trả lời bị cắt (quá dài) — hãy giảm số câu mỗi lần dịch.")
        return content

    def _complete_gemini(self, system: str, user: str) -> str:
        from google.genai import errors, types

        client = self._gemini()
        try:
            r = client.models.generate_content(
                model=self.model, contents=user,
                config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json"))
        except errors.ClientError as e:
            raise LLMError(f"Lỗi Gemini ({e.code}): {e.message}") from e
        except errors.APIError as e:
            raise LLMError(f"Lỗi Gemini: {e.message}") from e
        if not r.text:
            raise LLMError("Gemini không trả về nội dung (có thể bị chặn bởi bộ lọc an toàn).")
        return r.text

    # ----------------------------------------------------------------- misc
    def list_models(self) -> list[str]:
        if self.provider == "anthropic":
            return [m.id for m in self._anthropic().models.list()]
        if self.provider == "gemini":
            return [m.name.replace("models/", "") for m in self._gemini().models.list()]
        return sorted(m.id for m in self._openai().models.list())

    def ping(self) -> str:
        r = self.complete_json("Reply with JSON only.", 'Return {"ok": true, "lang": "vi"} as JSON.',
                               {"type": "object", "properties": {"ok": {"type": "boolean"}, "lang": {"type": "string"}},
                                "required": ["ok", "lang"], "additionalProperties": False}, max_tokens=2000, attempts=1)
        return json.dumps(r, ensure_ascii=False)
