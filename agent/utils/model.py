from typing import Literal, TypedDict, Unpack
import os

from langchain.chat_models import init_chat_model

OPENAI_RESPONSES_WS_BASE_URL = "wss://api.openai.com/v1"

# For free models on OpenRouter/OpenCode Zen, we need very aggressive retries.
DEFAULT_MAX_RETRIES = 20

DEFAULT_LLM_REASONING: "OpenAIReasoning" = {"effort": "medium"}

OpenAIReasoningEffort = Literal["none", "low", "medium", "high", "xhigh"]
AnthropicThinkingType = Literal["adaptive"]
AnthropicEffort = Literal["low", "medium", "high", "xhigh", "max"]
GoogleThinkingLevel = Literal["minimal", "low", "medium", "high"]


class OpenAIReasoning(TypedDict, total=False):
    effort: OpenAIReasoningEffort


class AnthropicThinking(TypedDict, total=False):
    type: AnthropicThinkingType


class ModelKwargs(TypedDict, total=False):
    max_tokens: int | None
    reasoning: OpenAIReasoning | None
    thinking: AnthropicThinking | None
    effort: AnthropicEffort | None
    thinking_level: GoogleThinkingLevel | None
    temperature: float | None
    max_retries: int | None


_ANTHROPIC_EFFORTS: set[AnthropicEffort] = {"low", "medium", "high", "xhigh", "max"}


_KNOWN_PROVIDERS = {
    "anthropic",
    "anthropic_bedrock",
    "azure_ai",
    "azure_openai",
    "baseten",
    "bedrock",
    "bedrock_converse",
    "cohere",
    "deepseek",
    "fireworks",
    "google_anthropic_vertex",
    "google_genai",
    "google_vertexai",
    "groq",
    "huggingface",
    "ibm",
    "litellm",
    "mistralai",
    "nvidia",
    "ollama",
    "openai",
    "openrouter",
    "perplexity",
    "together",
    "upstage",
    "xai",
}


def make_model(model_id: str, **kwargs: Unpack[ModelKwargs]):
    model_kwargs: dict[str, object] = kwargs.copy()
    model_kwargs.setdefault("max_retries", DEFAULT_MAX_RETRIES)

    actual_model = model_id
    model_provider = None

    if ":" in model_id:
        prefix, rest = model_id.split(":", 1)
        if prefix in _KNOWN_PROVIDERS:
            model_provider = prefix
            actual_model = rest

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    is_custom_openai = base_url != "https://api.openai.com/v1"
    
    # If no recognized provider prefix was found, but we have a custom OpenAI base URL,
    # assume the openai provider (using the custom endpoint as a drop-in replacement).
    if not model_provider and is_custom_openai:
        model_provider = "openai"
        actual_model = model_id

    if model_provider == "openai":
        model_kwargs["base_url"] = base_url
        # For non-standard OpenAI endpoints, we usually don't want to use the responses API
        if is_custom_openai:
            model_kwargs["use_responses_api"] = False

    return init_chat_model(model=actual_model, model_provider=model_provider, **model_kwargs)


def fallback_model_id_for(primary_model_id: str) -> str | None:
    """Return the cross-provider fallback model id for a given primary, if any.

    Anthropic primaries fall back to OpenAI and vice versa. Returns ``None``
    when the provider has no configured cross-provider fallback (e.g. Google,
    local, or self-hosted providers we don't want to silently route off-host).
    """
    if primary_model_id.startswith("anthropic:"):
        return "openai:gpt-5.5"
    if primary_model_id.startswith("openai:"):
        return "anthropic:claude-opus-4-5"
    return None


def is_gemini_3_family(model_id: str) -> bool:
    model_name = model_id.split(":", 1)[-1]
    return model_name.startswith("gemini-3")


def openai_reasoning_for(
    profile_effort: str | None,
    *,
    default_effort: OpenAIReasoningEffort | None = None,
) -> OpenAIReasoning | None:
    """Return an OpenAI reasoning kwarg from a profile effort string."""
    effort = profile_effort or default_effort or DEFAULT_LLM_REASONING.get("effort")
    if effort == "none":
        return {"effort": "none"}
    if effort == "low":
        return {"effort": "low"}
    if effort == "medium":
        return {"effort": "medium"}
    if effort == "high":
        return {"effort": "high"}
    if effort == "xhigh":
        return {"effort": "xhigh"}
    return None


def anthropic_thinking_for(profile_effort: str | None) -> AnthropicThinking | None:
    if profile_effort in _ANTHROPIC_EFFORTS:
        return {"type": "adaptive"}
    return None


def anthropic_effort_for(profile_effort: str | None) -> AnthropicEffort | None:
    if profile_effort in _ANTHROPIC_EFFORTS:
        return profile_effort
    return None


def google_thinking_level_for(profile_effort: str | None) -> GoogleThinkingLevel | None:
    """Map profile effort to Gemini 3+ ``thinking_level``."""
    if profile_effort == "none":
        return "minimal"
    if profile_effort == "low":
        return "low"
    if profile_effort == "medium":
        return "medium"
    if profile_effort in ("high", "xhigh", "max"):
        return "high"
    return None


def is_openai_reasoning_model(model_id: str) -> bool:
    """Return True if the model is an OpenAI o-series reasoning model."""
    model_name = model_id.split(":", 1)[-1]
    return model_name.startswith(("o1-", "o3-", "o1-preview", "o1-mini"))


def provider_model_kwargs(
    model_id: str,
    profile_effort: str | None,
    *,
    max_tokens: int,
    openai_reasoning_default: OpenAIReasoning | None = None,
) -> ModelKwargs:
    """Build provider-specific kwargs for ``make_model`` from a model id and effort."""
    kwargs: ModelKwargs = {"max_tokens": max_tokens}
    if model_id.startswith("openai:"):
        # Only add reasoning parameters for models that actually support them (o1, o3).
        # Passing 'reasoning' to non-reasoning models or 3rd party providers
        # causes TypeErrors in the underlying OpenAI client.
        if is_openai_reasoning_model(model_id):
            reasoning = openai_reasoning_for(profile_effort)
            if reasoning is not None:
                kwargs["reasoning"] = reasoning
            elif openai_reasoning_default is not None:
                kwargs["reasoning"] = openai_reasoning_default
    elif model_id.startswith("anthropic:"):
        thinking = anthropic_thinking_for(profile_effort)
        if thinking is not None:
            kwargs["thinking"] = thinking
        effort = anthropic_effort_for(profile_effort)
        if effort is not None:
            kwargs["effort"] = effort
    elif model_id.startswith("google_genai:") and is_gemini_3_family(model_id):
        thinking_level = google_thinking_level_for(profile_effort)
        if thinking_level is not None:
            kwargs["thinking_level"] = thinking_level
    return kwargs
