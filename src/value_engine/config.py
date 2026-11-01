"""Central configuration: every threshold the rules use lives here."""
from pathlib import Path

# Project root (this file is src/value_engine/config.py). Absolute paths let the CLI and the
# web app run from any working directory.
ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"

# --- Analysis window -------------------------------------------------------
YEARS = 10                 # years shown in the report and judged by the rules
MIN_YEARS = 7              # below this many valid years a rule reports INSUFFICIENT_DATA
GOOD_YEARS_FRACTION = 0.8  # "8 of 10 years" style rules scale with available years

# --- Graham (margin of safety) ---------------------------------------------
MAX_LOSS_YEARS = 0
INTEREST_COVERAGE_MIN = 3.0

# --- Buffett (moat & cash) -------------------------------------------------
ROCE_MIN_PCT = 15.0
ROE_MIN_PCT = 15.0
DEBT_TO_EQUITY_MAX = 0.5
FCF_TREND_WINDOW = 3       # compare avg of first N years vs last N years

# --- Fisher (quality & growth) ---------------------------------------------
MARGIN_SLOPE_MIN = -0.25   # pct-points per year; tolerates noise, "stable or expanding"
CFO_TO_NP_MIN = 0.8        # cumulative CFO / cumulative Net Profit
MAX_DIVERGENCE_YEARS = 2

# --- Earnings quality ------------------------------------------------------
DEBTOR_DAYS_MAX_RATIO = 1.3   # latest debtor days vs 10-yr median
DEBTOR_DAYS_SAFE = 30         # at or below this, a rising ratio is just noise on a small base
OTHER_INCOME_MAX_PCT = 25.0   # other income as % of profit before tax (3-yr avg)
TAX_RATE_MIN_PCT = 15.0       # median effective tax rate, last 5 yrs
EPS_LAG_MAX_PP = 2.0          # EPS CAGR may trail net-profit CAGR by at most this

# --- Banks / NBFCs ---------------------------------------------------------
BANK_ROA_MIN_PCT = 1.0
DEPOSIT_CAGR_MIN_PCT = 10.0
NII_SLOPE_MIN = -0.1          # pct-points per year

# --- Valuation -------------------------------------------------------------
GRAHAM_PE_MAX = 15.0
GRAHAM_NUMBER_FACTOR = 22.5   # Graham: P/E 15 x P/B 1.5
DISCOUNT_RATE = 0.12          # typical cost of equity for Indian large caps
TERMINAL_GROWTH = 0.05        # long-run nominal growth after the forecast period
DCF_YEARS = 10
VALUATION_PASS_FRACTION = 0.6 # share of valuation rules to call a stock "cheap enough"
QUALITY_STRONG_FRACTION = 0.8
QUALITY_MIXED_FRACTION = 0.6

# --- Phase 2: annual reports + Claude --------------------------------------
REPORTS_DIR = DATA_DIR / "reports"
MEMOS_DIR = DATA_DIR / "memos"
AI_PROVIDER = "anthropic"     # default for --ai; override per run with --provider / --model
AI_MAX_TOKENS = 16000
AI_EFFORT = "high"            # Anthropic thinking depth: low | medium | high | xhigh | max
AI_REFUSAL_FALLBACK = True    # Anthropic: re-run on a fallback model if a safety filter declines
OPENAI_REASONING_EFFORT = None  # e.g. "high" for OpenAI reasoning models; None = model default

# Each provider needs its OWN key in its OWN environment variable.
AI_PROVIDERS = {
    "anthropic": {"label": "Claude", "model": "claude-opus-5-5", "env": ["ANTHROPIC_API_KEY"]},
    "openai": {"label": "OpenAI", "model": "gpt-6.1-sol", "env": ["OPENAI_API_KEY"]},
    "google": {"label": "Gemini", "model": "gemini-3.8-flash", "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"]},
    "deepseek": {"label": "DeepSeek", "model": "deepseek-v4-pro", "env": ["DEEPSEEK_API_KEY"],
                 "base_url": "https://api.deepseek.com"},
    # Any OpenAI-compatible endpoint (Mistral, Groq, OpenRouter, a local Ollama...). Set
    # OPENAI_COMPATIBLE_BASE_URL and OPENAI_COMPATIBLE_API_KEY, and pass --model.
    "openai-compatible": {"label": "LLM", "model": None, "env": ["OPENAI_COMPATIBLE_API_KEY"],
                          "base_url_env": "OPENAI_COMPATIBLE_BASE_URL"},
}

# USD per 1M tokens, from each provider's official pricing page (checked 2026-11-01).
# Thinking/reasoning tokens bill as output. Unknown models show "cost unknown".
AI_PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "gpt-6-astra": {"input": 10.00, "output": 50.00, "cache_read": 1.00},
    "gpt-6.1-sol": {"input": 2.00, "output": 10.00, "cache_read": 0.10},
    "gpt-6.1-terra": {"input": 1.00, "output": 5.00, "cache_read": 0.05},
    "gpt-6-luna": {"input": 0.10, "output": 0.50, "cache_read": 0.01},
    "gemini-4-argon": {"input": 2.00, "output": 10.00},        # doubles from 2026-12-01
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75},       # doubles from 2027-01-01; free tier exists
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
    "deepseek-v4-pro": {"input": 1.32, "output": 3.96, "cache_read": 0.044},  # peak rate; off-peak is half
    "deepseek-flash": {"input": 0.30, "output": 1.20, "cache_read": 0.006},
}
EXCERPT_MAX_CHARS = 400_000   # hard cap on report text sent per call (~100k tokens)
CONTINGENT_LIABILITY_MAX_PCT = 10.0  # of net worth
BANK_GNPA_MAX_PCT = 3.0
BANK_CASA_MIN_PCT = 35.0

# --- Data fetching ---------------------------------------------------------
SCREENER_BASE = "https://www.screener.in"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_DELAY_SEC = 2.0
REQUEST_TIMEOUT_SEC = 30
CACHE_TTL_DAYS = 7

DB_PATH = DATA_DIR / "value_engine.db"
UNITS = "Rs Crores"
