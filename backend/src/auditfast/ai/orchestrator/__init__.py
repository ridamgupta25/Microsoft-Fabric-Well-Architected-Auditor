"""Model routing and provider abstraction.

One interface over Azure OpenAI, Claude, and Gemini so the choice of vendor is a
configuration value rather than a code change. Owns retries, timeouts, token
budgets, and fallback between providers.
"""
