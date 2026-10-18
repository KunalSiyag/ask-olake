import os

from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
# Order chosen by eval (README): DeepSeek was most accurate and fast; GLM was slow (p95 17 s)
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek/deepseek-v4.1-flash")
LLM_FALLBACK_MODELS = [m.strip() for m in os.getenv(
    "LLM_FALLBACK_MODELS", "google/gemini-2.5-flash-lite,z-ai/glm-4.7-flash").split(",") if m.strip()]
MAX_ANSWER_TOKENS = int(os.getenv("MAX_ANSWER_TOKENS", "800"))
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "25"))
HEDGE_AFTER_S = float(os.getenv("HEDGE_AFTER_S", "6"))  # 0 disables hedged requests

TOP_K = int(os.getenv("TOP_K", "8"))
MAX_QUESTION_CHARS = int(os.getenv("MAX_QUESTION_CHARS", "1000"))

# Abuse / budget guards. The graders send the test set from one IP, so the per-IP
# limit is generous; the global daily cap is what protects the $3 credit.
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "120"))
DAILY_LLM_CALL_CAP = int(os.getenv("DAILY_LLM_CALL_CAP", "3000"))
