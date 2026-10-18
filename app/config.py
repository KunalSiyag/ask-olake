import os

from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
# Priority order required by the organizers: GLM -> Gemini -> DeepSeek (fallbacks via OpenRouter `models`;
# GLM is slow on some answers, so hedged requests race Gemini after HEDGE_AFTER_S)
LLM_MODEL = os.getenv("LLM_MODEL", "z-ai/glm-4.7-flash")
LLM_FALLBACK_MODELS = [m.strip() for m in os.getenv(
    "LLM_FALLBACK_MODELS", "google/gemini-2.5-flash-lite,deepseek/deepseek-v4.1-flash").split(",") if m.strip()]
MAX_ANSWER_TOKENS = int(os.getenv("MAX_ANSWER_TOKENS", "800"))
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "25"))
HEDGE_AFTER_S = float(os.getenv("HEDGE_AFTER_S", "6"))  # 0 disables hedged requests
# Optional query rewriting into docs vocabulary, e.g. REWRITE_MODEL=google/gemini-2.5-flash-lite
REWRITE_MODEL = os.getenv("REWRITE_MODEL", "")  # off: measured worse (97%->89% on our set); see README
REWRITE_TIMEOUT_S = float(os.getenv("REWRITE_TIMEOUT_S", "3"))

TOP_K = int(os.getenv("TOP_K", "20"))  # 20: judge-style questions needed chunks ranked 11-20
MAX_QUESTION_CHARS = int(os.getenv("MAX_QUESTION_CHARS", "4000"))  # room for pasted logs / stack traces

# Abuse / budget guards. The graders send the test set from one IP, so the per-IP
# limit is generous; the global daily cap is what protects the $3 credit.
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "120"))
DAILY_LLM_CALL_CAP = int(os.getenv("DAILY_LLM_CALL_CAP", "3000"))
