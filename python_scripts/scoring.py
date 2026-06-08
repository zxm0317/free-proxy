import math
import random

# Bandit preset weights
BANDIT_PRESETS = {
    'priority': {'reliability': 0, 'speed': 0, 'intelligence': 0},
    'balanced': {'reliability': 0.5, 'speed': 0.25, 'intelligence': 0.25},
    'smartest': {'reliability': 0.35, 'speed': 0.1, 'intelligence': 0.55},
    'fastest': {'reliability': 0.35, 'speed': 0.55, 'intelligence': 0.1},
    'reliable': {'reliability': 0.7, 'speed': 0.15, 'intelligence': 0.15}
}

PRIOR_SUCCESS = 1
PRIOR_FAILURE = 1

def reliability_posterior(successes: int, failures: int) -> dict[str, int]:
    return {
        'alpha': max(0, successes) + PRIOR_SUCCESS,
        'beta': max(0, failures) + PRIOR_FAILURE
    }

def expected_reliability(successes: int, failures: int) -> float:
    posterior = reliability_posterior(successes, failures)
    return posterior['alpha'] / (posterior['alpha'] + posterior['beta'])

# In free-proxy we do not track TTFB or token throughput currently.
# To keep the visual metrics stable, we can create a deterministic
# pseudo-random value based on the model name.
def _deterministic_hash(s: str) -> float:
    h = 5381
    for c in s:
        h = ((h << 5) + h) + ord(c)
    # Return a float between 0 and 1
    return (h & 0xFFFFFFFF) / 0xFFFFFFFF

def synthetic_speed_score(provider: str, model: str) -> float:
    # Deterministic speed based on name, with some common knowledge biases
    # Usually groq is fast.
    if 'groq' in provider.lower():
        return 0.8 + 0.2 * _deterministic_hash(f"{provider}:{model}:speed")
    return 0.3 + 0.5 * _deterministic_hash(f"{provider}:{model}:speed")

def is_chat_candidate_model(provider: str, model: str) -> bool:
    name = f'{provider}/{model}'.lower()
    excluded = (
        'guard', 'moderation', 'whisper', 'audio', 'tts', 'speech',
        'embedding', 'embed', 'rerank', 'ranker', 'vision', 'vl', 'vision-preview',
        'image', 'imagen', 'stable-diffusion', 'flux',
    )
    return not any(token in name for token in excluded)

def synthetic_intelligence_score(provider: str, model: str) -> float:
    import re
    name = f'{provider}/{model}'.lower()

    qoder_scores = {
        'qoder/qmodel_latest': 97,
        'qoder/qmodel': 90,
        'qoder/dmodel': 98,
        'qoder/dfmodel': 88,
        'qoder/gm51model': 98,
        'qoder/kmodel': 97,
        'qoder/mmodel': 88,
        'qoder/ultimate': 96,
        'qoder/performance': 94,
        'qoder/efficient': 86,
        'qoder/lite': 78,
        'qoder/auto': 94,
    }
    if name in qoder_scores:
        return qoder_scores[name] / 100
    
    # Base score selection based on family
    family_rules = (
        (('gpt-5',), 99),
        (('deepseek-v4',), 98),
        (('glm-5', 'glm5'), 98),
        (('kimi-k2.6', 'kimi-k2-thinking'), 97),
        (('gpt-4.1',), 96),
        (('gpt-4o', 'gpt-4'), 95),
        (('claude-3-opus', 'opus', 'sonnet'), 95),
        (('deepseek-r1', 'deepseek-v3'), 95),
        (('gemma-4', 'gemma4'), 93),
        (('gemini-3',), 93),
        (('qwen3-coder-next', 'qwen3-next'), 93),
        (('llama-4',), 92),
        (('qwen3-coder',), 90),
        (('nemotron-3',), 88),
        (('qwen3',), 86),
        (('llama-3.3',), 85),
        (('mistral-large',), 84),
        (('gpt-oss',), 82),
        (('mistral-medium',), 78),
        (('codestral', 'mistral-code'), 75),
    )
    
    base_score = 70
    for tokens, val in family_rules:
        matched = False
        for token in tokens:
            pattern = rf'(?<![a-zA-Z0-9]){re.escape(token)}(?![a-zA-Z0-9])'
            if re.search(pattern, name):
                matched = True
                break
        if matched:
            base_score = val
            break
            
    # Size/Type Modifiers
    modifier = 0
    
    # 1. Size parameter rules
    size_matched = False
    size_rules = (
        (('480b', '405b', '235b'), 4),
        (('120b', '110b'), 2),
        (('70b', '72b'), 0),
        (('32b', '31b'), -4),
        (('26b', '24b', '22b'), -6),
        (('17b', '14b', '12b'), -8),
        (('8b', '7b'), -10),
    )
    for tokens, mod in size_rules:
        matched = False
        for token in tokens:
            pattern = rf'(?<![a-zA-Z0-9]){re.escape(token)}(?![a-zA-Z0-9])'
            if re.search(pattern, name):
                matched = True
                break
        if matched:
            modifier = mod
            size_matched = True
            break
            
    # 2. Type indicators rules (only if no explicit parameter size was matched)
    if not size_matched:
        if any(token in name for token in ('nano',)):
            modifier = -15
        elif any(token in name for token in ('mini', 'small', 'lite', 'flash')):
            modifier = -8
            
    # 3. Penalty rules for non-chat/special models
    penalty_rules = (
        (('guard', 'moderation', 'whisper', 'audio', 'embedding', 'rerank'), -45),
    )
    for tokens, pen in penalty_rules:
        matched = False
        for token in tokens:
            pattern = rf'(?<![a-zA-Z0-9]){re.escape(token)}(?![a-zA-Z0-9])'
            if re.search(pattern, name):
                matched = True
                break
        if matched:
            modifier += pen
            break
            
    score = base_score + modifier
    
    # Special rules (compatibility)
    if 'gpt-4.1-mini' in name or ('gpt-5' in name and 'mini' in name):
        score += 5
        
    return max(0.2, min(0.99, score / 100))

def headroom_factor(remaining_requests: int | None) -> float:
    if remaining_requests is None or remaining_requests > 10:
        return 1.0
    return max(0.1, remaining_requests / 10.0)

def combine_score(
    reliability: float,
    speed: float,
    intelligence: float,
    headroom: float,
    rate_limit: float,
    weights: dict[str, float]
) -> float:
    w_sum = weights['reliability'] + weights['speed'] + weights['intelligence']
    if w_sum <= 0:
        return 0.0
    base = (
        weights['reliability'] * reliability +
        weights['speed'] * speed +
        weights['intelligence'] * intelligence
    ) / w_sum
    return base * headroom * rate_limit

def route_priority_metrics(
    provider: str,
    model: str,
    health_entry: dict[str, object] | None = None,
) -> dict[str, float]:
    entry = health_entry if isinstance(health_entry, dict) else {}
    success_streak = int(entry.get('success_streak', 0) or 0)
    failure_streak = int(entry.get('failure_streak', 0) or 0)
    return {
        'intelligence': synthetic_intelligence_score(provider, model),
        'speed': synthetic_speed_score(provider, model),
        'reliability': expected_reliability(success_streak, failure_streak),
    }

def route_priority_sort_key(
    provider: str,
    model: str,
    health_entry: dict[str, object] | None = None,
) -> tuple[float, float, float]:
    metrics = route_priority_metrics(provider, model, health_entry)
    return (
        metrics['intelligence'],
        metrics['speed'],
        metrics['reliability'],
    )

def get_model_limits(provider: str, model: str) -> dict[str, str | int | None]:
    p = provider.lower()
    m = model.lower()
    
    # Defaults
    monthly_budget = "~18M"
    rpm = 10
    rpd = 50
    
    if "github" in p:
        if "gpt-4o" in m:
            monthly_budget = "~18M"
            rpm = 10
            rpd = 50
        elif "mini" in m:
            monthly_budget = "~45M"
            rpm = 15
            rpd = 150
        else:
            monthly_budget = "~30M"
            rpm = 10
            rpd = 100
    elif "groq" in p:
        monthly_budget = "~120M"
        rpm = 30
        rpd = 14400
    elif "sambanova" in p:
        monthly_budget = "~120M"
        rpm = 20
        rpd = 1000
    elif "mistral" in p:
        monthly_budget = "~6M"
        rpm = 5
        rpd = 100
    elif "gemini" in p:
        monthly_budget = "~15M"
        rpm = 15
        rpd = 1500
        
    return {
        "monthly_token_budget": monthly_budget,
        "rpm_limit": rpm,
        "rpd_limit": rpd
    }
