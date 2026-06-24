from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable, Literal


FormatType = Literal['openai', 'gemini', 'anthropic']


@dataclass(frozen=True)
class ProviderMeta:
    name: str
    base_url: str
    api_key_env: str
    format: FormatType
    model_hints: tuple[str, ...] = field(default_factory=tuple)
    required_query: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    model_capabilities: dict[str, dict[str, object]] = field(default_factory=dict)


PROVIDERS: list[ProviderMeta] = [
    ProviderMeta(
        'github',
        'https://models.github.ai/inference',
        'GITHUB_MODELS_API_KEY',
        'openai',
        model_hints=('gpt-4o', 'DeepSeek-V3-0324', 'gpt-4.1-mini', 'gpt-4o-mini'),
        required_query=(('api-version', '2024-12-01-preview'),),
    ),
    ProviderMeta(
        'google',
        'https://generativelanguage.googleapis.com/v1beta',
        'GOOGLE_API_KEY',
        'gemini',
        model_hints=('gemini-2.0-flash', 'gemini-3.1-flash-lite-preview'),
    ),
    ProviderMeta(
        'gemini',
        'https://generativelanguage.googleapis.com/v1beta',
        'GEMINI_API_KEY',
        'gemini',
        model_hints=('gemini-2.0-flash', 'gemini-3.1-flash-lite-preview'),
    ),
    ProviderMeta(
        'groq',
        'https://api.groq.com/openai/v1',
        'GROQ_API_KEY',
        'openai',
        model_hints=('llama-3.3-70b-versatile', 'llama-3.1-8b-instant'),
    ),
    ProviderMeta(
        'cerebras',
        'https://api.cerebras.ai/v1',
        'CEREBRAS_API_KEY',
        'openai',
        model_hints=('llama3.1-8b', 'llama3.1-70b'),
    ),
    ProviderMeta(
        'sambanova',
        'https://api.sambanova.ai/v1',
        'SAMBANOVA_API_KEY',
        'openai',
        model_hints=('DeepSeek-V3.1-Terminus', 'Qwen3-235B', 'Meta-Llama-3.1-8B-Instruct'),
    ),
    ProviderMeta(
        'nvidia',
        'https://integrate.api.nvidia.com/v1',
        'NVIDIA_API_KEY',
        'openai',
        model_hints=('meta/llama-3.1-70b-instruct',),
    ),
    ProviderMeta(
        'mistral',
        'https://api.mistral.ai/v1',
        'MISTRAL_API_KEY',
        'openai',
        model_hints=('mistral-large-latest', 'mistral-medium-latest', 'mistral-small-latest'),
    ),
    ProviderMeta(
        'openrouter',
        'https://openrouter.ai/api/v1',
        'OPENROUTER_API_KEY',
        'openai',
        model_hints=('openrouter/auto:free',),
    ),
    ProviderMeta(
        'cohere',
        'https://api.cohere.com/v1',
        'COHERE_API_KEY',
        'openai',
        model_hints=('command-r-plus',),
    ),
    ProviderMeta(
        'cloudflare',
        'https://api.cloudflare.com/client/v4/accounts',
        'CLOUDFLARE_API_KEY',
        'openai',
        model_hints=('@cf/meta/llama-3-8b-instruct',),
    ),
    ProviderMeta(
        'zhipu',
        'https://open.bigmodel.cn/api/paas/v4',
        'ZHIPU_API_KEY',
        'openai',
        model_hints=('glm-4-flash',),
    ),
    ProviderMeta(
        'ollama',
        'https://ollama.com/v1',
        'OLLAMA_API_KEY',
        'openai',
        model_hints=(
            'glm-5.1',
            'minimax-m2.1',
            'minimax-m2.5',
            'kimi-k2.6',
            'deepseek-v3.1:671b',
            'glm-5.2',
            'qwen3-coder:480b',
            'deepseek-v4-flash',
            'gpt-oss:120b',
            'nemotron-3-nano:30b',
            'gemma4:31b',
            'glm-5',
            'devstral-small-2:24b',
            'nemotron-3-super',
            'nemotron-3-ultra',
            'qwen3-coder-next',
            'devstral-2:123b',
            'kimi-k2.5',
            'kimi-k2.7-code',
            'deepseek-v3.2',
            'ministral-3:14b',
            'glm-4.7',
            'deepseek-v4-pro',
            'ministral-3:3b',
            'ministral-3:8b',
            'mistral-large-3:675b',
            'gemini-3-flash-preview',
            'qwen3.5:397b',
            'gpt-oss:20b',
            'minimax-m2.7',
            'minimax-m3',
            'gemma3:4b',
            'gemma3:12b',
            'gemma3:27b',
            'rnj-1:8b',
        ),
    ),
    ProviderMeta(
        'kilo',
        'https://api.kilo.ai/api/gateway/v1',
        'KILO_API_KEY',
        'openai',
        model_hints=('kilo/auto:free',),
    ),
    ProviderMeta(
        'pollinations',
        'https://text.pollinations.ai/openai/v1',
        'POLLINATIONS_API_KEY',
        'openai',
        model_hints=('openai-fast',),
    ),
    ProviderMeta(
        'llm7',
        'https://api.llm7.io/v1',
        'LLM7_API_KEY',
        'openai',
        model_hints=('llm7/auto:free',),
    ),
    ProviderMeta(
        'huggingface',
        'https://router.huggingface.co/v1',
        'HUGGINGFACE_API_KEY',
        'openai',
        model_hints=('meta-llama/Llama-3-8B-Instruct',),
    ),
    ProviderMeta(
        'opencode',
        'https://opencode.ai/zen/v1',
        'OPENCODE_API_KEY',
        'openai',
        model_hints=('free-proxy/auto',),
    ),
    ProviderMeta(
        'qoder',
        'https://api3.qoder.sh',
        'QODER_ACCOUNT_TOKEN',
        'openai',
        model_hints=(
            'auto',
            'ultimate',
            'performance',
            'efficient',
            'lite',
            'qmodel',
            'qmodel_latest',
            'dmodel',
            'dfmodel',
            'gm51model',
            'kmodel',
            'mmodel',
        ),
    ),
]
PROVIDER_MAP: dict[str, ProviderMeta] = {provider.name: provider for provider in PROVIDERS}


def clear_custom_providers() -> None:
    stale_names = [provider.name for provider in PROVIDERS if provider.name.startswith('custom-')]
    if not stale_names:
        return
    stale = set(stale_names)
    PROVIDERS[:] = [provider for provider in PROVIDERS if provider.name not in stale]
    for name in stale:
        PROVIDER_MAP.pop(name, None)


def register_custom_provider(name: str, base_url: str, api_key_env: str, format: FormatType, model_hints: Iterable[str]) -> None:
    global PROVIDERS, PROVIDER_MAP
    for p in list(PROVIDERS):
        if p.name == name:
            PROVIDERS.remove(p)
    meta = ProviderMeta(
        name=name,
        base_url=base_url,
        api_key_env=api_key_env,
        format=format,
        model_hints=tuple(model_hints),
    )
    PROVIDERS.append(meta)
    PROVIDER_MAP[name] = meta


def get_provider(name: str) -> ProviderMeta:
    provider = PROVIDER_MAP.get(name)
    if provider is None:
        raise KeyError(f'unknown provider: {name}')
    return provider


def list_providers(names: Iterable[str] | None = None) -> list[ProviderMeta]:
    if names is None:
        return list(PROVIDERS)
    wanted = set(names)
    return [provider for provider in PROVIDERS if provider.name in wanted]


def configured_provider_names(env: dict[str, str] | None = None) -> list[str]:
    source = os.environ if env is None else env
    return [provider.name for provider in PROVIDERS if str(source.get(provider.api_key_env, '')).strip()]


def get_provider_model_hints(name: str) -> list[str]:
    return list(get_provider(name).model_hints)


def get_provider_required_query(name: str) -> dict[str, str]:
    return dict(get_provider(name).required_query)


def get_model_capabilities(name: str, model_id: str) -> dict[str, object]:
    provider = get_provider(name)
    model_key = model_id.strip()
    if model_key and model_key in provider.model_capabilities:
        return dict(provider.model_capabilities[model_key])
    return {}
