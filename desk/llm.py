"""One decision interface over the Anthropic SDK and LiteLLM provider routing."""
import json
import os
from typing import Literal, TypeVar

from anthropic import Anthropic
from pydantic import BaseModel, Field, SecretStr

DEFAULT_MODEL = "anthropic/claude-sonnet-5-5"
Result = TypeVar("Result", bound=BaseModel)


class AIError(Exception):
    """The configured provider could not produce a validated decision."""


class AIConfig(BaseModel):
    model: str = Field(min_length=3)
    mode: Literal["auto", "live", "demo"] = "auto"
    output_mode: Literal["schema", "json"] = "schema"
    api_key: SecretStr = SecretStr("")
    api_base: str = ""

    @property
    def provider(self):
        return self.model.split("/", 1)[0]

    @property
    def enabled(self):
        if self.mode != "auto":
            return self.mode == "live"
        return bool(self.api_key.get_secret_value() or self.api_base or self.provider in ("ollama", "ollama_chat"))

    @property
    def label(self):
        return {"anthropic": "Anthropic", "openai": "OpenAI", "gemini": "Gemini",
                "ollama": "Ollama", "ollama_chat": "Ollama"}.get(self.provider, self.provider)


def configuration():
    model = os.getenv("LLM_MODEL") or DEFAULT_MODEL
    if "/" not in model:
        raise ValueError("LLM_MODEL must include its provider, for example anthropic/claude-sonnet-5-5")
    provider = model.split("/", 1)[0]
    key_env = os.getenv("LLM_API_KEY_ENV") or f"{provider.upper()}_API_KEY"
    return AIConfig(
        model=model,
        mode=os.getenv("LLM_MODE") or "auto",
        output_mode=os.getenv("LLM_OUTPUT_MODE") or "schema",
        api_key=os.getenv("LLM_API_KEY") or os.getenv(key_env) or "",
        api_base=os.getenv("LLM_API_BASE") or "",
    )


def generate(instructions: str, payload: dict, result_type: type[Result]) -> Result:
    config = configuration()
    if not config.enabled:
        raise AIError("AI is disabled")
    try:
        if config.provider == "anthropic":
            return _anthropic(config, instructions, payload, result_type)
        import litellm

        options = {
            "model": config.model,
            "timeout": 30,
            "num_retries": 1,
            "max_tokens": 1600,
        }
        if config.api_key.get_secret_value():
            options["api_key"] = config.api_key.get_secret_value()
        if config.api_base:
            options["api_base"] = config.api_base
        if config.output_mode == "schema":
            options["response_format"] = result_type
        else:
            instructions += "\nReturn only one JSON object matching this schema:\n" + json.dumps(result_type.model_json_schema())
        response = litellm.completion(
            messages=[{"role": "system", "content": instructions},
                      {"role": "user", "content": json.dumps(payload)}],
            **options,
        )
        choice = response.choices[0]
        if (choice.finish_reason not in ("stop", "tool_calls")
                or choice.message.tool_calls or not choice.message.content):
            raise ValueError("Provider returned an incomplete decision")
        return result_type.model_validate_json(choice.message.content)
    except Exception as exc:
        # Keep provider details out of customer replies and expose one error boundary.
        raise AIError(f"{config.provider} decision failed") from exc


def _anthropic(config: AIConfig, instructions: str, payload: dict, result_type: type[Result]) -> Result:
    options = {"timeout": 30, "max_retries": 1}
    if config.api_key.get_secret_value():
        options["api_key"] = config.api_key.get_secret_value()
    if config.api_base:
        options["base_url"] = config.api_base
    request = {
        "model": config.model.split("/", 1)[1],
        "max_tokens": 1600,
        "system": instructions,
        "messages": [{"role": "user", "content": json.dumps(payload)}],
    }
    with Anthropic(**options) as client:
        if config.output_mode == "schema":
            # The official SDK handles Claude's schema constraints and validates
            # against the original Pydantic model after receiving the response.
            response = client.messages.parse(output_format=result_type, **request)
            if response.stop_reason not in ("end_turn", "stop_sequence") or response.parsed_output is None:
                raise ValueError("Anthropic returned an incomplete decision")
            return response.parsed_output
        request["system"] += "\nReturn only one JSON object matching this schema:\n" + json.dumps(result_type.model_json_schema())
        response = client.messages.create(**request)
        if response.stop_reason not in ("end_turn", "stop_sequence"):
            raise ValueError("Anthropic returned an incomplete decision")
        content = "".join(block.text for block in response.content if block.type == "text")
        return result_type.model_validate_json(content)
