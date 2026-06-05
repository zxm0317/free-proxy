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

def synthetic_intelligence_score(provider: str, model: str) -> float:
    # Deterministic intelligence
    val = _deterministic_hash(f"{provider}:{model}:intel")
    model_lower = model.lower()
    if 'gpt-4' in model_lower or 'claude-3-opus' in model_lower or 'sonnet' in model_lower:
        return 0.85 + 0.15 * val
    elif 'llama-3' in model_lower and '70b' in model_lower:
        return 0.75 + 0.15 * val
    return 0.2 + 0.6 * val

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
