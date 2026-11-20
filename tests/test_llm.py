import json

import httpx
import litellm
import pytest
from anthropic import Anthropic

from desk.intents import CustomerTurn
from desk.llm import AIError, DEFAULT_MODEL, configuration, generate


@pytest.fixture(autouse=True)
def clean_provider_environment(monkeypatch):
    for name in ("LLM_MODE", "LLM_MODEL", "LLM_API_KEY", "LLM_API_KEY_ENV", "LLM_API_BASE",
                 "LLM_OUTPUT_MODE", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_anthropic_default_and_explicit_provider_selection(monkeypatch):
    assert configuration().model == DEFAULT_MODEL
    assert configuration().provider == "anthropic"
    assert not configuration().enabled
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    assert not configuration().enabled
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    assert configuration().enabled
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-4.1-mini")
    assert configuration().api_key.get_secret_value() == "unrelated-key"
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    assert configuration().api_key.get_secret_value() == "generic-key"
    monkeypatch.setenv("LLM_MODE", "demo")
    assert not configuration().enabled


def test_anthropic_structured_output_through_real_provider_translation(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-key")
    requests = []
    decision = CustomerTurn(action="offer", issue="Pipe leak").model_dump(mode="json")

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert request.url.host == "api.anthropic.com"
        assert request.headers["x-api-key"] == "test-only-key"
        assert payload["model"] == "claude-sonnet-5-5"
        assert "1402" in json.dumps(payload["messages"])
        schema = payload["output_config"]["format"]["schema"]
        assert "maxLength" not in schema["properties"]["body"]
        assert "maximum" not in schema["properties"]["choice"]["anyOf"][0]
        return httpx.Response(200, json={
            "id":"msg_test", "type":"message", "role":"assistant", "model":"claude-sonnet-5-5",
            "stop_reason":"end_turn", "stop_sequence":None,
            "content":[{"type":"text", "text":json.dumps(decision)}],
            "usage":{"input_tokens":20,"output_tokens":30},
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
        monkeypatch.setattr("desk.llm.Anthropic", lambda **kwargs: Anthropic(**kwargs, http_client=http_client))
        result = generate("Return a decision.", {"gate":"1402"}, CustomerTurn)
    assert result.issue == "Pipe leak"
    assert len(requests) == 1


@pytest.mark.parametrize("model,mode", [("gemini/example-model","schema"), ("openai/custom-model","json"), ("ollama_chat/local-model","json")])
def test_other_providers_share_validated_adapter(monkeypatch, model, mode):
    monkeypatch.setenv("LLM_MODEL", model)
    monkeypatch.setenv("LLM_API_KEY", "provider-key")
    monkeypatch.setenv("LLM_API_BASE", "http://localhost:1234/v1")
    monkeypatch.setenv("LLM_OUTPUT_MODE", mode)

    def complete(**kwargs):
        assert kwargs["model"] == model
        assert kwargs["api_key"] == "provider-key"
        assert kwargs["api_base"] == "http://localhost:1234/v1"
        if mode == "schema":
            assert kwargs["response_format"] is CustomerTurn
        else:
            assert "response_format" not in kwargs
            assert '"properties"' in kwargs["messages"][0]["content"]
        return litellm.ModelResponse(choices=[{"finish_reason":"stop", "message":{"role":"assistant", "content":'{"action":"reply","body":"Hello"}'}}])

    monkeypatch.setattr(litellm, "completion", complete)
    assert generate("Return a decision.", {}, CustomerTurn).body == "Hello"


@pytest.mark.parametrize("content,finish", [('not JSON','stop'), ('{"action":"delete_everything"}','stop'), ('{"action":"reply"}','length')])
def test_invalid_or_incomplete_output_is_rejected(monkeypatch, content, finish):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_MODEL", "openai/custom-model")
    monkeypatch.setattr(litellm, "completion", lambda **kw: litellm.ModelResponse(choices=[{"finish_reason":finish, "message":{"role":"assistant","content":content}}]))
    with pytest.raises(AIError):
        generate("Return a decision.", {}, CustomerTurn)
