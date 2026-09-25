"""
System Configuration & Environment Settings
US + Canada Market Intelligence Platform
"""

import os
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


class Settings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Environment
    ENVIRONMENT: str = Field(default=os.getenv("ENVIRONMENT", "development"))
    DEBUG: bool = Field(default=os.getenv("DEBUG", "true").lower() == "true")

    # Database
    # Default to local SQLite database for zero-cost instant development;
    # On production (e.g., Oracle Always Free ARM), override with:
    # DATABASE_URL=postgresql://user:pass@localhost:5432/market_intel
    DATABASE_URL: str = Field(
        default=os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR}/market_intel.db")
    )

    # SEC EDGAR API (Mandatory User-Agent header)
    # Required format: SampleAppName AdminContact@domain.com
    SEC_EDGAR_USER_AGENT: str = Field(
        default=os.getenv(
            "SEC_EDGAR_USER_AGENT",
            "MarketIntelPlatform/1.0 (dev-ops@marketintel.local)",
        )
    )
    SEC_EDGAR_MAX_REQ_PER_SEC: float = 8.0  # Safe buffer under SEC 10 req/s limit

    # Bank of Canada Valet API
    BOC_VALET_BASE_URL: str = "https://www.bankofcanada.ca/valet"
    BOC_VALET_MAX_REQ_PER_SEC: float = 5.0

    # Federal Reserve Economic Data (FRED)
    FRED_API_KEY: str = Field(default=os.getenv("FRED_API_KEY", ""))
    FRED_BASE_URL: str = "https://api.stlouisfed.org/fred"
    FRED_MAX_REQ_PER_MIN: float = 100.0  # Safe buffer under 120 req/min limit

    # AI / LLM Inference Providers (Free Tiers)
    GROQ_API_KEY: str = Field(default=os.getenv("GROQ_API_KEY", ""))
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_DEFAULT_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_MAX_REQ_PER_MIN: float = 25.0  # Safe buffer under 30 req/min

    GEMINI_API_KEY: str = Field(default=os.getenv("GEMINI_API_KEY", ""))
    GEMINI_DEFAULT_MODEL: str = "gemini-2.5-flash"

    # Compliance & Data Quality
    STRICT_PROVENANCE_REQUIRED: bool = True
    ALLOW_MOCK_FALLBACK: bool = Field(
        default=os.getenv("ALLOW_MOCK_FALLBACK", "false").lower() == "true"
    )


# Global singleton settings instance
settings = Settings()
