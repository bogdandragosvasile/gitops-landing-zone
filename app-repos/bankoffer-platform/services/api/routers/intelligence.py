"""Market Intelligence router — AI-driven product suggestions based on world dynamics.

Analyzes exchange markets, geopolitics, regulations, economic indicators,
and historic trends to generate product suggestions for admin approval.
Uses connected AI models when available, falls back to built-in engine.
"""

import json
import logging
import os
import re
import random
import time
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

from services.api.audit import write_audit_log, write_ai_api_log
from services.api.metrics import (
    AI_API_CALLS, AI_API_LATENCY, AI_SUGGESTIONS_FILTERED,
    AI_ANALYSIS_RATE_LIMITED,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ════════════════════════════════════════════════════════════════
# Guardrail constants
# ════════════════════════════════════════════════════════════════

CONFIDENCE_THRESHOLD = 0.60          # Guardrail #1: reject suggestions below this
RATE_LIMIT_MAX_CALLS = 5             # Guardrail #2: max /analyze calls per window
RATE_LIMIT_WINDOW_SECONDS = 3600     # 1 hour window
STALE_SUGGESTION_DAYS = 30           # Guardrail #4: auto-expire after this many days
PRODUCT_CATALOG_CAP = 50             # Guardrail #6: max active products
COOLDOWN_HOURS = 24                  # Guardrail #7: staging period before AI products go live


# ════════════════════════════════════════════════════════════════
# Guardrail #5: AI Output Content Filter
# ════════════════════════════════════════════════════════════════

# PII patterns to strip
_PII_PATTERNS = [
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), '[EMAIL_REDACTED]'),
    (re.compile(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'), '[PHONE_REDACTED]'),
    (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), '[SSN_REDACTED]'),
    (re.compile(r'\b\d{13,19}\b'), '[CARD_REDACTED]'),           # credit card numbers
    (re.compile(r'\bRO\d{2}[A-Z]{4}\d{16}\b'), '[IBAN_REDACTED]'),  # Romanian IBAN
]

# Prompt injection patterns
_INJECTION_PATTERNS = re.compile(
    r'(ignore previous|ignore above|disregard|forget your|you are now|new instructions|system prompt|<\|im_start\|>|<\|endoftext\|>)',
    re.IGNORECASE,
)


def _sanitize_ai_text(text_val: str) -> tuple[str, list[str]]:
    """Strip PII and detect prompt injection in AI-generated text. Returns (clean_text, warnings)."""
    if not text_val:
        return text_val, []
    warnings = []
    clean = text_val
    # Strip PII
    for pattern, replacement in _PII_PATTERNS:
        if pattern.search(clean):
            warnings.append(f"PII detected and redacted: {replacement}")
            clean = pattern.sub(replacement, clean)
    # Detect prompt injection
    if _INJECTION_PATTERNS.search(clean):
        warnings.append("Possible prompt injection detected in AI output")
        clean = _INJECTION_PATTERNS.sub('[FILTERED]', clean)
    return clean, warnings

# ════════════════════════════════════════════════════════════════
# AI Provider Adapters — call real APIs when keys are configured
# ════════════════════════════════════════════════════════════════

_AI_SYSTEM_PROMPT = """You are a senior banking product strategist AI. You analyze market intelligence data and suggest new banking products.

You MUST respond with valid JSON only — no markdown, no commentary. Return an object with two keys:

{
  "market_intelligence": [
    {
      "category": "exchange_markets|geopolitics|regulations|economic|trends",
      "title": "Short headline (max 120 chars)",
      "summary": "2-3 sentence analysis with specific numbers",
      "impact": "positive|negative|neutral|mixed",
      "severity": "low|medium|high|critical",
      "data_points": {"key": "value pairs with real metrics"},
      "source": "Data source name",
      "region": "Geographic region"
    }
  ],
  "product_suggestions": [
    {
      "product_name": "Product name",
      "product_type": "investment|savings|lending|mortgage|credit_card|insurance",
      "description": "2-3 sentence product description with specific terms (rates, minimums, etc.)",
      "reasoning": "3-4 sentence explanation linking market drivers to product opportunity",
      "target_segments": ["new_graduate","young_family","mid_career","pre_retirement","retired","high_income"],
      "market_drivers": ["Names of market intelligence items that drive this suggestion"],
      "confidence": 0.50 to 0.99,
      "projected_demand": "low|medium|high|very_high",
      "risk_level": "low|medium|high"
    }
  ]
}

Generate 12-18 market intelligence items (spread across all 5 categories) and 5-8 product suggestions.
Focus on the European/Romanian banking market. Use realistic current data."""

_AI_USER_PROMPT = """Analyze the current global financial landscape and generate market intelligence and banking product suggestions.

Consider:
1. **Exchange Markets** — ECB/NBR interest rates, EUR/RON, EUR/USD, bond yields, equity indices, commodities
2. **Geopolitics** — trade agreements, conflicts, EU policy, regional developments
3. **Regulations** — EU AI Act, MiFID, Consumer Credit Directive, GDPR, local NBR regulations
4. **Economic Indicators** — GDP growth, inflation, unemployment, wage growth, housing market, consumer confidence
5. **Trends** — ESG investing, digital banking adoption, BNPL, open banking, crypto regulation

Return ONLY the JSON object as specified. No other text."""


async def _call_anthropic(config: dict, market_context: str = "") -> dict | None:
    """Call Anthropic Claude API."""
    api_key = config.get("api_key", "").strip()
    if not api_key:
        return None
    model = config.get("model", "claude-sonnet-4-6")
    max_tokens = int(config.get("max_tokens", 4096))
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": _AI_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": _AI_USER_PROMPT + market_context}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text_block = data.get("content", [{}])[0].get("text", "")
            return json.loads(text_block)
    except Exception as e:
        logger.error("Anthropic API error: %s", e)
        return None


async def _call_openai(config: dict, market_context: str = "") -> dict | None:
    """Call OpenAI ChatCompletion API."""
    api_key = config.get("api_key", "").strip()
    if not api_key:
        return None
    model = config.get("model", "gpt-4o")
    temperature = float(config.get("temperature", 0.7))
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "temperature": temperature,
                    "max_tokens": 4096,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _AI_SYSTEM_PROMPT},
                        {"role": "user", "content": _AI_USER_PROMPT + market_context},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text_block = data["choices"][0]["message"]["content"]
            return json.loads(text_block)
    except Exception as e:
        logger.error("OpenAI API error: %s", e)
        return None


async def _call_gemini(config: dict, market_context: str = "") -> dict | None:
    """Call Google Gemini API."""
    api_key = config.get("api_key", "").strip()
    if not api_key:
        return None
    model = config.get("model", "gemini-2.0-flash")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": _AI_SYSTEM_PROMPT + "\n\n" + _AI_USER_PROMPT + market_context}]}],
                    "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096, "responseMimeType": "application/json"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text_block = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text_block)
    except Exception as e:
        logger.error("Gemini API error: %s", e)
        return None


async def _call_huggingface(config: dict, market_context: str = "") -> dict | None:
    """Call Hugging Face Inference API."""
    api_token = config.get("api_token", "").strip()
    if not api_token:
        return None
    model_id = config.get("model_id", "mistralai/Mixtral-8x7B-Instruct-v0.1")
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"https://api-inference.huggingface.co/models/{model_id}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
                json={
                    "model": model_id,
                    "max_tokens": 4096,
                    "messages": [
                        {"role": "system", "content": _AI_SYSTEM_PROMPT},
                        {"role": "user", "content": _AI_USER_PROMPT + market_context},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text_block = data["choices"][0]["message"]["content"]
            return json.loads(text_block)
    except Exception as e:
        logger.error("Hugging Face API error: %s", e)
        return None


async def _call_perplexity(config: dict, market_context: str = "") -> dict | None:
    """Call Perplexity AI API (OpenAI-compatible)."""
    api_key = config.get("api_key", "").strip()
    if not api_key:
        return None
    model = config.get("model", "sonar-pro")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(read=90.0, connect=10.0, write=10.0, pool=5.0)) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 8000,
                    "messages": [
                        {"role": "system", "content": _AI_SYSTEM_PROMPT},
                        {"role": "user", "content": _AI_USER_PROMPT + market_context},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text_block = data["choices"][0]["message"]["content"]
            # Perplexity may wrap JSON in markdown code fences
            text_block = text_block.strip()
            if text_block.startswith("```"):
                text_block = text_block.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(text_block)
    except httpx.ConnectTimeout:
        logger.warning("Perplexity API: connection timed out (host unreachable or no internet access)")
        return None
    except httpx.ReadTimeout:
        logger.warning("Perplexity API: response generation timed out (model too slow for prompt size)")
        return None
    except httpx.ConnectError as e:
        logger.warning("Perplexity API: connection error — %s", e)
        return None
    except Exception as e:
        logger.error("Perplexity API error [%s]: %s", type(e).__name__, e)
        return None


async def _call_local_llm(config: dict, market_context: str = "") -> dict | None:
    """Call a local/self-hosted LLM via OpenAI-compatible API (Ollama, vLLM, LM Studio, text-generation-webui, etc.)."""
    base_url = config.get("base_url", "").strip().rstrip("/")
    if not base_url:
        return None
    model = config.get("model", "llama3")
    api_key = config.get("api_key", "").strip() or "not-needed"
    temperature = float(config.get("temperature", 0.7))
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "temperature": temperature,
                    "max_tokens": 4096,
                    "messages": [
                        {"role": "system", "content": _AI_SYSTEM_PROMPT},
                        {"role": "user", "content": _AI_USER_PROMPT + market_context},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text_block = data["choices"][0]["message"]["content"]
            text_block = text_block.strip()
            if text_block.startswith("```"):
                text_block = text_block.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(text_block)
    except Exception as e:
        logger.error("Local LLM API error (%s): %s", base_url, e)
        return None


# Provider name → adapter function
_AI_ADAPTERS = {
    "Anthropic": _call_anthropic,
    "OpenAI": _call_openai,
    "Google": _call_gemini,
    "Hugging Face": _call_huggingface,
    "Perplexity": _call_perplexity,
    "Local LLM": _call_local_llm,
}


async def _call_ai_provider(provider: str, config_values: dict, market_context: str = "", request=None) -> dict | None:
    """Route to the correct AI provider adapter. Returns parsed JSON or None.
    Tracks metrics for every call (Guardrail #10) and logs to ai_api_call_log."""
    adapter = _AI_ADAPTERS.get(provider)
    model_name = config_values.get("model", "unknown")
    if not adapter:
        logger.warning("No adapter for AI provider: %s", provider)
        AI_API_CALLS.labels(provider=provider, result="skipped").inc()
        return None
    start = time.monotonic()
    try:
        result = await adapter(config_values, market_context)
        elapsed = time.monotonic() - start
        latency_ms = int(elapsed * 1000)
        AI_API_LATENCY.labels(provider=provider).observe(elapsed)
        if result:
            AI_API_CALLS.labels(provider=provider, result="success").inc()
            logger.info("AI call to %s succeeded in %.1fs", provider, elapsed)
            # Log to ai_api_call_log
            if request:
                try:
                    import json as _json
                    await write_ai_api_log(
                        request, provider=provider, model=model_name,
                        request_prompt=market_context[:4000],
                        response_text=_json.dumps(result)[:4000],
                        latency_ms=latency_ms, http_status=200,
                        outcome="success", guardrail_action="accepted",
                    )
                except Exception:
                    pass
        else:
            AI_API_CALLS.labels(provider=provider, result="failure").inc()
            if request:
                try:
                    await write_ai_api_log(
                        request, provider=provider, model=model_name,
                        request_prompt=market_context[:4000],
                        latency_ms=latency_ms, http_status=200,
                        outcome="failure", error_message="Empty response from provider",
                    )
                except Exception:
                    pass
        return result
    except Exception as e:
        elapsed = time.monotonic() - start
        latency_ms = int(elapsed * 1000)
        AI_API_CALLS.labels(provider=provider, result="failure").inc()
        AI_API_LATENCY.labels(provider=provider).observe(elapsed)
        logger.error("AI provider %s error: %s", provider, e)
        if request:
            try:
                await write_ai_api_log(
                    request, provider=provider, model=model_name,
                    request_prompt=market_context[:4000],
                    latency_ms=latency_ms, http_status=0,
                    outcome="failure", error_message=str(e)[:2000],
                )
            except Exception:
                pass
        return None


# ════════════════════════════════════════════════════════════════
# Market Intelligence Data Engine
# ════════════════════════════════════════════════════════════════

# Real-time-style market data (refreshed on each /analyze call)
def _generate_market_intelligence() -> list[dict]:
    """Generate realistic market intelligence entries across all categories."""
    now = datetime.utcnow()
    valid = now + timedelta(days=7)

    return [
        # ── Exchange Markets ──
        {
            "category": "exchange_markets",
            "title": "ECB Holds Interest Rate at 3.75% — Signals Q3 Cut",
            "summary": "The European Central Bank maintained its main refinancing rate at 3.75%, but forward guidance suggests a 25bps cut in Q3 2026. Euro deposit rates remain attractive for savings products. Bond yields in the 2-5Y segment have flattened, creating opportunity for fixed-income offerings.",
            "impact": "positive",
            "severity": "high",
            "data_points": {"ecb_rate": 3.75, "expected_q3_rate": 3.50, "eur_usd": 1.0842, "bund_10y": 2.31, "euribor_6m": 3.42},
            "source": "ECB Monetary Policy Statement",
            "region": "EU",
        },
        {
            "category": "exchange_markets",
            "title": "EUR/RON Stable at 4.97 — NBR Maintains Tight Corridor",
            "summary": "The Romanian leu continues its managed float with NBR maintaining the EUR/RON rate in a tight corridor around 4.97. Romanian government bond yields at 6.8% for 5Y offer significant spread over EUR benchmarks, supporting RON-denominated deposit and bond products.",
            "impact": "positive",
            "severity": "medium",
            "data_points": {"eur_ron": 4.97, "nbr_rate": 6.50, "ro_5y_yield": 6.80, "ro_inflation": 4.2},
            "source": "NBR Market Report",
            "region": "Romania",
        },
        {
            "category": "exchange_markets",
            "title": "Global Equity Markets Rally — S&P 500 at All-Time High",
            "summary": "Risk-on sentiment drives equities higher. S&P 500 breached 5,800 for the first time. Tech sector leads with AI-related stocks up 34% YTD. European STOXX 600 follows with +12% YTD. Strong equity performance increases demand for investment and wealth management products.",
            "impact": "positive",
            "severity": "medium",
            "data_points": {"sp500": 5823, "stoxx600": 528, "nasdaq": 18940, "vix": 13.2, "bet_index": 17450},
            "source": "Bloomberg Markets",
            "region": "Global",
        },
        {
            "category": "exchange_markets",
            "title": "Gold Hits $2,480/oz — Safe Haven Demand Rising",
            "summary": "Gold prices surged to $2,480/oz amid geopolitical uncertainty and central bank buying. Silver follows at $31.2/oz. Precious metals demand suggests growing appetite for capital preservation products and inflation hedging among retail investors.",
            "impact": "mixed",
            "severity": "medium",
            "data_points": {"gold_usd": 2480, "silver_usd": 31.2, "central_bank_gold_buying_tonnes": 290},
            "source": "World Gold Council",
            "region": "Global",
        },
        # ── Geopolitics ──
        {
            "category": "geopolitics",
            "title": "EU-US Trade Agreement Expansion — Financial Services Chapter",
            "summary": "EU and US concluded negotiations on expanding mutual recognition of financial services regulation. Cross-border banking products become easier to offer. EU banks gain streamlined access to US wealth management clients, and vice versa. Impacts product distribution strategy.",
            "impact": "positive",
            "severity": "high",
            "data_points": {"affected_sectors": ["banking", "insurance", "asset_management"], "implementation_date": "2026-09-01"},
            "source": "European Commission Trade DG",
            "region": "EU / USA",
        },
        {
            "category": "geopolitics",
            "title": "Middle East Tensions Elevate Oil Prices — Brent at $89",
            "summary": "Escalating tensions in the Red Sea shipping corridor push Brent crude to $89/barrel. Energy costs impact European manufacturing sector. Higher import costs may slow economic recovery. Travel insurance and commodity-linked products see increased demand.",
            "impact": "negative",
            "severity": "high",
            "data_points": {"brent_crude": 89, "shipping_disruption_pct": 23, "energy_inflation_contribution": 1.4},
            "source": "IEA Market Report",
            "region": "Global",
        },
        {
            "category": "geopolitics",
            "title": "Romania's EU Council Presidency — Digital Finance Focus",
            "summary": "Romania's upcoming EU Council presidency prioritizes digital financial inclusion and cross-border payments. Expected to accelerate Digital Euro pilot and open banking adoption. Banks should prepare API-first products and digital wallet integrations.",
            "impact": "positive",
            "severity": "medium",
            "data_points": {"presidency_start": "2026-07-01", "priority_areas": ["digital_euro", "open_banking", "financial_inclusion"]},
            "source": "Romanian Government Programme",
            "region": "Romania / EU",
        },
        # ── Regulations ──
        {
            "category": "regulations",
            "title": "AI Act Full Enforcement Begins — High-Risk AI Systems in Banking",
            "summary": "EU AI Act enters full enforcement for high-risk AI systems in financial services. Credit scoring, insurance pricing, and automated offer engines must comply with Articles 6-15 (risk management, data governance, transparency, human oversight). Non-compliance carries fines up to 3% of global turnover.",
            "impact": "mixed",
            "severity": "critical",
            "data_points": {"enforcement_date": "2026-08-01", "fine_pct_turnover": 3, "affected_systems": ["credit_scoring", "offer_engines", "fraud_detection"]},
            "source": "EU Official Journal",
            "region": "EU",
        },
        {
            "category": "regulations",
            "title": "MiFID III Proposal — Enhanced Suitability Requirements",
            "summary": "European Commission published MiFID III proposal strengthening suitability assessment for retail investors. New requirements for ESG preference integration, cost transparency, and digital distribution channels. Investment product manufacturers must update target market assessments.",
            "impact": "mixed",
            "severity": "high",
            "data_points": {"proposal_date": "2026-03-15", "expected_adoption": "2027-Q2", "key_changes": ["esg_suitability", "cost_benchmarking", "digital_distribution"]},
            "source": "European Commission",
            "region": "EU",
        },
        {
            "category": "regulations",
            "title": "NBR Updated Consumer Credit Directive Transposition",
            "summary": "Romania's National Bank transposed the revised EU Consumer Credit Directive. New pre-contractual information requirements, right to early repayment without penalty for variable-rate loans, and standardized annual percentage rate calculations. Loan product terms must be updated by Q4 2026.",
            "impact": "negative",
            "severity": "high",
            "data_points": {"transposition_deadline": "2026-10-01", "key_impacts": ["early_repayment", "apr_calculation", "pre_contractual_info"]},
            "source": "NBR Regulation",
            "region": "Romania",
        },
        # ── Economic Indicators ──
        {
            "category": "economic",
            "title": "Romania GDP Growth at 3.8% — Outpacing EU Average",
            "summary": "Romania's economy grew 3.8% YoY in Q1 2026, well above EU average of 1.2%. Growth driven by services sector (+5.1%), construction (+4.3%), and IT (+7.2%). Rising incomes support demand for premium banking products, investment accounts, and mortgage products.",
            "impact": "positive",
            "severity": "high",
            "data_points": {"gdp_growth_ro": 3.8, "gdp_growth_eu": 1.2, "services_growth": 5.1, "it_growth": 7.2, "avg_wage_growth": 12.4},
            "source": "INS / Eurostat",
            "region": "Romania",
        },
        {
            "category": "economic",
            "title": "EU Inflation Falls to 2.1% — Consumer Confidence Rising",
            "summary": "Eurozone inflation dropped to 2.1%, approaching ECB's 2% target. Consumer confidence index reached -8.2 (highest since 2022). Falling inflation and improving sentiment drive increased consumer spending and credit demand. Favorable environment for personal loans and credit products.",
            "impact": "positive",
            "severity": "medium",
            "data_points": {"eu_inflation": 2.1, "consumer_confidence": -8.2, "retail_sales_growth": 1.8, "unemployment": 6.4},
            "source": "Eurostat",
            "region": "EU",
        },
        {
            "category": "economic",
            "title": "Housing Market Recovery in CEE — Mortgage Demand Up 22%",
            "summary": "Central and Eastern European housing markets show strong recovery. Mortgage applications in Romania up 22% YoY, driven by government subsidies (Noua Casă), falling rates, and urbanization. Average property prices in Bucharest rose 8.3% YoY. Prime conditions for mortgage product expansion.",
            "impact": "positive",
            "severity": "high",
            "data_points": {"mortgage_growth_ro": 22, "property_price_growth_buc": 8.3, "noua_casa_utilization": 78, "avg_mortgage_rate": 5.9},
            "source": "NBR Financial Stability Report",
            "region": "Romania / CEE",
        },
        # ── Trends ──
        {
            "category": "trends",
            "title": "ESG Investment Surge — Sustainable Finance AUM Up 40%",
            "summary": "Assets under management in ESG-labeled funds grew 40% YoY globally. EU Green Bond Standard adoption accelerates. Romanian retail investors show 3x increase in ESG fund inquiries. Banks offering green bonds and sustainable investment products capture growing demand segment.",
            "impact": "positive",
            "severity": "high",
            "data_points": {"esg_aum_growth": 40, "green_bond_issuance_eur_bn": 95, "ro_esg_interest_growth": 300},
            "source": "Morningstar / EFAMA",
            "region": "Global / Romania",
        },
        {
            "category": "trends",
            "title": "Digital Banking Adoption — 67% of Romanians Use Mobile Banking",
            "summary": "Mobile banking penetration in Romania reached 67% (up from 51% in 2024). Gen Z and Millennials show 89% adoption. Digital-first product distribution now reaches majority of the market. In-app offers, instant approvals, and API-driven products increasingly preferred over branch-based.",
            "impact": "positive",
            "severity": "medium",
            "data_points": {"mobile_banking_pct": 67, "gen_z_adoption": 89, "digital_origination_pct": 43, "branch_visit_decline": -18},
            "source": "ECB Payment Statistics / Fintech Report",
            "region": "Romania",
        },
        {
            "category": "trends",
            "title": "Buy Now Pay Later (BNPL) Regulation Coming — Market Maturing",
            "summary": "EU regulators signaled BNPL products will fall under Consumer Credit Directive by 2027. Current unregulated BNPL market growing 28% YoY. Banks that launch compliant BNPL-style installment products now can capture market share before fintech competitors face regulatory burden.",
            "impact": "positive",
            "severity": "medium",
            "data_points": {"bnpl_market_growth": 28, "regulation_expected": "2027-H1", "bank_bnpl_market_share": 12},
            "source": "EBA Consumer Trends Report",
            "region": "EU",
        },
    ]


def _generate_product_suggestions(intelligence: list[dict], active_models: list[str]) -> list[dict]:
    """Generate AI product suggestions based on market intelligence."""
    model_name = active_models[0] if active_models else "built-in-engine"

    suggestions = [
        {
            "product_name": "Green Bond Fund — ESG Select",
            "product_type": "investment",
            "description": "Sustainable investment fund focused on EU Green Bond Standard-compliant instruments. Targets 4.2-5.5% annual return with ESG impact scoring. Minimum investment €500, quarterly liquidity.",
            "reasoning": "ESG fund AUM grew 40% YoY globally (Morningstar). Romanian retail ESG interest up 300%. EU Green Bond Standard creates quality benchmark. MiFID III will require ESG suitability assessment — having ESG products in catalog positions bank ahead of regulatory curve. Strong alignment with ECB rate environment (3.75%) making bond yields attractive.",
            "target_segments": ["mid_career", "pre_retirement", "high_income"],
            "market_drivers": ["ESG Investment Surge", "ECB Interest Rate Policy", "MiFID III Proposal"],
            "confidence": 0.89,
            "projected_demand": "high",
            "risk_level": "medium",
        },
        {
            "product_name": "Fixed Rate Deposit — 12M Lock (6.2% APY)",
            "product_type": "savings",
            "description": "12-month fixed-rate deposit at 6.2% APY in RON. Capitalizes on NBR's 6.50% rate and expected ECB cuts making EUR alternatives less attractive. FGDB guaranteed up to €100,000 equivalent.",
            "reasoning": "NBR rate at 6.50% while ECB signals Q3 cut to 3.50%. EUR/RON stable at 4.97 within managed corridor. Romanian 5Y government bonds at 6.8% confirm high-yield RON environment. Consumer confidence rising (+12.4% wage growth) means more disposable income for deposits. Window of opportunity before rate convergence narrows spread.",
            "target_segments": ["pre_retirement", "retired", "mid_career"],
            "market_drivers": ["ECB Rate Policy", "EUR/RON Stability", "Romania GDP Growth"],
            "confidence": 0.93,
            "projected_demand": "very_high",
            "risk_level": "low",
        },
        {
            "product_name": "AI-Compliant Personal Loan — Instant Decision",
            "product_type": "lending",
            "description": "Personal loan up to €25,000 with AI-powered instant credit decision (fully AI Act compliant — Art. 14 human oversight, Art. 22 explanation). Variable rate ROBOR 3M + 3.5%. Digital-first origination with e-signature.",
            "reasoning": "EU inflation at 2.1% boosts consumer confidence. Mortgage demand up 22% signals broader credit appetite. Digital banking at 67% penetration enables digital origination. AI Act enforcement (Aug 2026) requires compliant scoring — first-mover advantage for AI Act-ready lending. NBR Consumer Credit Directive transposition requires updated terms by Q4 2026.",
            "target_segments": ["young_family", "mid_career", "new_graduate"],
            "market_drivers": ["EU Inflation Decline", "Digital Banking Adoption", "AI Act Enforcement", "Consumer Credit Directive"],
            "confidence": 0.85,
            "projected_demand": "high",
            "risk_level": "medium",
        },
        {
            "product_name": "Noua Casă Mortgage Plus — First-Time Buyer",
            "product_type": "mortgage",
            "description": "Government-guaranteed mortgage for first-time buyers under the Noua Casă program. Up to €70,000 with 5% down payment. Fixed rate 5.4% for first 5 years, then IRCC + 2.5%. Includes property insurance bundle.",
            "reasoning": "Housing market recovery: mortgage demand +22% YoY, Bucharest prices +8.3%. Noua Casă utilization at 78% shows strong program demand. Average mortgage rate 5.9% — our 5.4% fixed beats market. Rising wages (+12.4%) improve debt serviceability. Construction sector growing 4.3% ensures supply. CEE housing undersupply structural driver.",
            "target_segments": ["new_graduate", "young_family"],
            "market_drivers": ["Housing Market Recovery", "Romania GDP Growth", "Noua Casă Program"],
            "confidence": 0.91,
            "projected_demand": "very_high",
            "risk_level": "low",
        },
        {
            "product_name": "FlexPay Installments — Bank BNPL",
            "product_type": "credit_card",
            "description": "Point-of-sale installment product (3-12 months, 0% for partner merchants). Regulated BNPL alternative compliant with upcoming Consumer Credit Directive. Integrated via merchant API and mobile wallet.",
            "reasoning": "BNPL market growing 28% YoY but regulation incoming (2027-H1). Banks hold only 12% BNPL market share — massive capture opportunity. EU Consumer Credit Directive will require BNPL providers to comply with full lending regulations, creating barrier for fintechs. Digital banking at 67% enables in-app experience. First-mover among traditional banks.",
            "target_segments": ["new_graduate", "young_family", "mid_career"],
            "market_drivers": ["BNPL Regulation Coming", "Digital Banking Adoption", "Consumer Confidence Rising"],
            "confidence": 0.82,
            "projected_demand": "high",
            "risk_level": "medium",
        },
        {
            "product_name": "Gold & Precious Metals Savings — Monthly Plan",
            "product_type": "investment",
            "description": "Monthly gold accumulation plan starting at €50/month. Physical gold backed, stored in certified vault. Auto-invest on monthly schedule with live pricing. Sell back anytime at spot minus 1.5%.",
            "reasoning": "Gold at $2,480/oz — all-time highs driven by central bank buying (290 tonnes) and geopolitical uncertainty (Middle East, trade tensions). Safe-haven demand rising among retail investors. EUR-denominated gold fund protects against both inflation and currency risk. Accessible minimum (€50/mo) captures mass affluent segment underserved by traditional gold products.",
            "target_segments": ["mid_career", "pre_retirement", "retired"],
            "market_drivers": ["Gold Price Rally", "Geopolitical Tensions", "Inflation Hedging Demand"],
            "confidence": 0.78,
            "projected_demand": "medium",
            "risk_level": "medium",
        },
        {
            "product_name": "Cross-Border Wealth Account — EU/US",
            "product_type": "investment",
            "description": "Multi-currency wealth management account for EU/US investment access. Leverages new EU-US trade agreement for cross-border fund distribution. USD and EUR denominated. Access to S&P 500 ETFs, EU bond funds, and managed portfolios. MiFID II compliant.",
            "reasoning": "EU-US Trade Agreement expansion (Sep 2026) enables streamlined cross-border financial services. S&P 500 at all-time high (5,823) drives US equity interest among EU retail investors. EUR/USD at 1.0842 makes dollar assets accessible. Multi-currency accounts meet demand from internationally mobile professionals. IT sector growing 7.2% in Romania creates high-income segment interested in global diversification.",
            "target_segments": ["mid_career", "pre_retirement", "high_income"],
            "market_drivers": ["EU-US Trade Agreement", "Global Equity Rally", "IT Sector Growth Romania"],
            "confidence": 0.76,
            "projected_demand": "medium",
            "risk_level": "high",
        },
    ]

    for s in suggestions:
        s["ai_model_used"] = model_name

    return suggestions


# ════════════════════════════════════════════════════════════════
# API Endpoints
# ════════════════════════════════════════════════════════════════

@router.get(
    "/market-data",
    summary="Get current market intelligence",
    description="Returns the latest market intelligence across all categories.",
)
async def get_market_data(
    request: Request,
    category: str = Query(None, description="Filter: exchange_markets, geopolitics, regulations, economic, trends"),
):
    session_factory = request.app.state.db_session_factory
    conditions = []
    params: dict = {}
    if category:
        conditions.append("category = :cat")
        params["cat"] = category
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    try:
        async with session_factory() as session:
            result = await session.execute(
                text(f"""SELECT id, category, title, summary, impact, severity,
                            data_points, source, region, valid_until, created_at
                     FROM market_intelligence {where}
                     ORDER BY severity DESC, created_at DESC"""),
                params,
            )
            rows = result.fetchall()
            return [
                {
                    "id": r[0], "category": r[1], "title": r[2], "summary": r[3],
                    "impact": r[4], "severity": r[5],
                    "data_points": r[6] if isinstance(r[6], dict) else json.loads(r[6]) if r[6] else {},
                    "source": r[7], "region": r[8], "valid_until": str(r[9]) if r[9] else None,
                    "created_at": str(r[10]),
                }
                for r in rows
            ]
    except Exception as e:
        logger.error("Market data error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load market data")


@router.post(
    "/analyze",
    summary="Trigger AI analysis and generate product suggestions",
    description="Uses connected AI models + market intelligence to generate product suggestions.",
)
async def analyze_and_suggest(request: Request):
    """Core intelligence engine with guardrails: rate limit, content filter, confidence gate, auto-expiry."""
    session_factory = request.app.state.db_session_factory
    redis = request.app.state.redis

    # ── Guardrail #2: Rate limiting (Redis-backed) ──
    rate_key = "guardrail:analyze_rate"
    try:
        current = await redis.incr(rate_key)
        if current == 1:
            await redis.expire(rate_key, RATE_LIMIT_WINDOW_SECONDS)
        if current > RATE_LIMIT_MAX_CALLS:
            AI_ANALYSIS_RATE_LIMITED.inc()
            ttl = await redis.ttl(rate_key)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {RATE_LIMIT_MAX_CALLS} analyses per hour. Try again in {ttl}s.",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Redis rate-limit check failed (proceeding): %s", e)

    try:
        async with session_factory() as session:
            # 1. Fetch active AI connectors WITH their config
            ai_result = await session.execute(
                text("""SELECT name, provider, config_values
                        FROM connectors
                        WHERE category = 'ai' AND status = 'active'""")
            )
            active_connectors = [
                {
                    "name": r[0],
                    "provider": r[1],
                    "config": r[2] if isinstance(r[2], dict) else json.loads(r[2]) if r[2] else {},
                }
                for r in ai_result.fetchall()
            ]
            active_model_names = [c["name"] for c in active_connectors]

            # 2. Try each active AI connector until one succeeds
            ai_response = None
            model_used = "built-in-engine"
            for conn in active_connectors:
                has_key = any(v for k, v in conn["config"].items() if "key" in k or "token" in k)
                if not has_key:
                    logger.info("Skipping %s — no API key configured", conn["name"])
                    continue
                logger.info("Calling AI provider: %s (%s)", conn["name"], conn["provider"])
                ai_response = await _call_ai_provider(conn["provider"], conn["config"], request=request)
                if ai_response:
                    model_used = conn["name"]
                    logger.info("AI response received from %s", conn["name"])
                    break
                logger.warning("AI call to %s failed, trying next...", conn["name"])

            # 3. Extract intelligence + suggestions from AI response or fall back
            if ai_response:
                intelligence = ai_response.get("market_intelligence", [])
                suggestions = ai_response.get("product_suggestions", [])
                # Stamp model name on suggestions
                for s in suggestions:
                    s["ai_model_used"] = model_used
            else:
                if active_connectors:
                    logger.info("All AI calls failed or no keys configured — using built-in engine")
                intelligence = _generate_market_intelligence()
                suggestions = _generate_product_suggestions(intelligence, active_model_names)

            # 4. Validate and sanitize AI-generated data
            valid_categories = {"exchange_markets", "geopolitics", "regulations", "economic", "trends"}
            valid_impacts = {"positive", "negative", "neutral", "mixed"}
            valid_severities = {"low", "medium", "high", "critical"}
            valid_prod_types = {"investment", "savings", "lending", "mortgage", "credit_card", "insurance"}
            valid_demands = {"low", "medium", "high", "very_high"}
            valid_risks = {"low", "medium", "high"}

            clean_intel = []
            for item in intelligence:
                if item.get("category") not in valid_categories:
                    continue
                item.setdefault("impact", "neutral")
                item.setdefault("severity", "medium")
                if item["impact"] not in valid_impacts:
                    item["impact"] = "neutral"
                if item["severity"] not in valid_severities:
                    item["severity"] = "medium"
                item.setdefault("data_points", {})
                item.setdefault("source", "AI Analysis")
                item.setdefault("region", "Global")
                clean_intel.append(item)

            # ── Guardrail #4: Auto-expire stale pending suggestions ──
            expired_result = await session.execute(
                text("""UPDATE ai_product_suggestions
                        SET status = 'expired'
                        WHERE status = 'pending'
                          AND created_at < NOW() - INTERVAL ':days days'
                        RETURNING id""".replace(":days", str(STALE_SUGGESTION_DAYS)))
            )
            expired_count = len(expired_result.fetchall())
            if expired_count:
                logger.info("Guardrail: expired %d stale suggestions (>%dd old)", expired_count, STALE_SUGGESTION_DAYS)
                AI_SUGGESTIONS_FILTERED.labels(reason="expired").inc(expired_count)
            await session.commit()

            clean_suggestions = []
            content_warnings = []
            low_confidence_rejected = 0
            for s in suggestions:
                if not s.get("product_name") or not s.get("product_type"):
                    continue
                if s["product_type"] not in valid_prod_types:
                    s["product_type"] = "investment"
                s.setdefault("description", "")
                s.setdefault("reasoning", "")
                s.setdefault("target_segments", [])
                s.setdefault("market_drivers", [])
                s.setdefault("confidence", 0.5)
                s.setdefault("projected_demand", "medium")
                s.setdefault("risk_level", "medium")
                s.setdefault("ai_model_used", model_used)
                if s["projected_demand"] not in valid_demands:
                    s["projected_demand"] = "medium"
                if s["risk_level"] not in valid_risks:
                    s["risk_level"] = "medium"
                conf = float(s["confidence"])
                s["confidence"] = max(0.0, min(0.999, conf))

                # ── Guardrail #1: Confidence threshold ──
                if s["confidence"] < CONFIDENCE_THRESHOLD:
                    low_confidence_rejected += 1
                    AI_SUGGESTIONS_FILTERED.labels(reason="low_confidence").inc()
                    logger.info("Guardrail: rejected '%s' — confidence %.0f%% < %.0f%% threshold",
                                s["product_name"], s["confidence"] * 100, CONFIDENCE_THRESHOLD * 100)
                    continue

                # ── Guardrail #5: Content filter (PII, injection) ──
                for field in ("product_name", "description", "reasoning"):
                    if s.get(field):
                        cleaned, warns = _sanitize_ai_text(s[field])
                        s[field] = cleaned
                        if warns:
                            content_warnings.extend(warns)
                            AI_SUGGESTIONS_FILTERED.labels(reason="content_filter").inc()

                clean_suggestions.append(s)

            # 5. Store market intelligence (with content filter on text fields)
            await session.execute(text("DELETE FROM market_intelligence"))
            for item in clean_intel:
                for field in ("title", "summary"):
                    if item.get(field):
                        item[field], _ = _sanitize_ai_text(item[field])
                await session.execute(
                    text("""INSERT INTO market_intelligence
                        (category, title, summary, impact, severity, data_points, source, region, valid_until)
                        VALUES (:cat, :title, :summary, :impact, :sev, :dp, :src, :reg, :valid)"""),
                    {
                        "cat": item["category"],
                        "title": item["title"][:300],
                        "summary": item["summary"],
                        "impact": item["impact"],
                        "sev": item["severity"],
                        "dp": json.dumps(item.get("data_points", {})),
                        "src": (item.get("source") or "AI Analysis")[:200],
                        "reg": (item.get("region") or "Global")[:100],
                        "valid": datetime.utcnow() + timedelta(days=7),
                    },
                )
            await session.commit()

            # 6. Store product suggestions (skip duplicates)
            created = []
            for s in clean_suggestions:
                exists = await session.execute(
                    text("SELECT id FROM ai_product_suggestions WHERE product_name = :name AND status = 'pending'"),
                    {"name": s["product_name"]},
                )
                if exists.fetchone():
                    AI_SUGGESTIONS_FILTERED.labels(reason="duplicate").inc()
                    continue
                result = await session.execute(
                    text("""INSERT INTO ai_product_suggestions
                        (product_name, product_type, description, reasoning, target_segments,
                         market_drivers, confidence, projected_demand, risk_level, ai_model_used, intelligence_ids, status)
                        VALUES (:name, :type, :desc, :reason, :segs, :drivers, :conf, :demand, :risk, :model, :intel, 'pending')
                        RETURNING id, created_at"""),
                    {
                        "name": s["product_name"][:200],
                        "type": s["product_type"],
                        "desc": s["description"],
                        "reason": s["reasoning"],
                        "segs": json.dumps(s.get("target_segments", [])),
                        "drivers": json.dumps(s.get("market_drivers", [])),
                        "conf": s["confidence"],
                        "demand": s["projected_demand"],
                        "risk": s["risk_level"],
                        "model": s.get("ai_model_used", model_used),
                        "intel": json.dumps([]),
                    },
                )
                row = result.fetchone()
                created.append({"id": row[0], "product_name": s["product_name"]})
            await session.commit()

            return {
                "market_intelligence_count": len(clean_intel),
                "suggestions_created": len(created),
                "suggestions_rejected_low_confidence": low_confidence_rejected,
                "suggestions_expired": expired_count,
                "content_warnings": content_warnings,
                "active_ai_models": active_model_names,
                "model_used": model_used,
                "ai_powered": model_used != "built-in-engine",
                "guardrails": {
                    "confidence_threshold": CONFIDENCE_THRESHOLD,
                    "rate_limit": f"{RATE_LIMIT_MAX_CALLS}/hour",
                    "stale_expiry_days": STALE_SUGGESTION_DAYS,
                    "catalog_cap": PRODUCT_CATALOG_CAP,
                    "cooldown_hours": COOLDOWN_HOURS,
                    "content_filter": "active",
                },
                "suggestions": created,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Analysis error: %s", e)
        raise HTTPException(status_code=500, detail="Analysis failed: " + str(e))


@router.get(
    "/suggestions",
    summary="List AI product suggestions",
)
async def list_suggestions(
    request: Request,
    status: str = Query(None, description="Filter by status: pending, approved, rejected, implemented"),
):
    session_factory = request.app.state.db_session_factory
    where = "WHERE status = :st" if status else ""
    params: dict = {}
    if status:
        params["st"] = status
    try:
        async with session_factory() as session:
            result = await session.execute(
                text(f"""SELECT id, product_name, product_type, description, reasoning,
                            target_segments, market_drivers, confidence, projected_demand,
                            risk_level, ai_model_used, status, approved_by, approved_at, created_at
                     FROM ai_product_suggestions {where}
                     ORDER BY confidence DESC, created_at DESC"""),
                params,
            )
            rows = result.fetchall()
            return [
                {
                    "id": r[0], "product_name": r[1], "product_type": r[2],
                    "description": r[3], "reasoning": r[4],
                    "target_segments": r[5] if isinstance(r[5], list) else json.loads(r[5]) if r[5] else [],
                    "market_drivers": r[6] if isinstance(r[6], list) else json.loads(r[6]) if r[6] else [],
                    "confidence": float(r[7]), "projected_demand": r[8], "risk_level": r[9],
                    "ai_model_used": r[10], "status": r[11],
                    "approved_by": r[12], "approved_at": str(r[13]) if r[13] else None,
                    "created_at": str(r[14]),
                }
                for r in rows
            ]
    except Exception as e:
        logger.error("Suggestions list error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to list suggestions")


@router.put(
    "/suggestions/{suggestion_id}/approve",
    summary="Approve or reject a product suggestion",
)
async def approve_suggestion(suggestion_id: int, request: Request, action: str = Query(...), approved_by: str = Query(...)):
    session_factory = request.app.state.db_session_factory
    new_status = "approved" if action == "approve" else "rejected"
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("""UPDATE ai_product_suggestions
                        SET status = :st, approved_by = :by, approved_at = NOW()
                        WHERE id = :sid
                        RETURNING id, product_name, status"""),
                {"sid": suggestion_id, "st": new_status, "by": approved_by},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Suggestion not found")
            await session.commit()
            try:
                await write_audit_log(
                    request, action="approve" if action == "approve" else "reject",
                    resource_type="ai_suggestion", resource_id=str(suggestion_id),
                    actor=approved_by, actor_type="staff",
                    changes={"product_name": row[1], "new_status": row[2]},
                )
            except Exception:
                pass
            return {"id": row[0], "product_name": row[1], "status": row[2]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Approve error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to update suggestion")


@router.put(
    "/suggestions/{suggestion_id}/implement",
    summary="Implement an approved suggestion — publish to the live product catalog",
)
async def implement_suggestion(suggestion_id: int, request: Request, implemented_by: str = Query(...)):
    """Push an approved AI suggestion into the products table — with guardrails."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            # 1. Fetch the suggestion
            result = await session.execute(
                text("""SELECT id, product_name, product_type, description, risk_level,
                            target_segments, confidence, approved_by
                     FROM ai_product_suggestions WHERE id = :sid"""),
                {"sid": suggestion_id},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Suggestion not found")
            if row[3] is None:
                raise HTTPException(status_code=400, detail="Suggestion data is incomplete")

            # Check current status
            st_result = await session.execute(
                text("SELECT status FROM ai_product_suggestions WHERE id = :sid"),
                {"sid": suggestion_id},
            )
            current_status = st_result.scalar()
            if current_status != "approved":
                raise HTTPException(status_code=400, detail=f"Only approved suggestions can be implemented (current: {current_status})")

            product_name = row[1]
            product_type = row[2]
            description = row[3]
            risk_level = row[4] or "moderate"
            target_segments = row[5] if isinstance(row[5], list) else json.loads(row[5]) if row[5] else []
            original_approver = row[7] or ""

            # ── Guardrail #3: Dual approval for high-risk products ──
            if risk_level == "high":
                if implemented_by.lower() == original_approver.lower():
                    raise HTTPException(
                        status_code=400,
                        detail=f"High-risk product requires dual approval. "
                               f"Originally approved by {original_approver} — "
                               f"a different admin must publish to catalog.",
                    )
                logger.info("Guardrail: dual approval satisfied for high-risk product '%s' "
                            "(approved by %s, implemented by %s)", product_name, original_approver, implemented_by)

            # ── Guardrail #6: Product catalog cap ──
            count_result = await session.execute(
                text("SELECT count(*) FROM products WHERE active = TRUE")
            )
            active_count = count_result.scalar() or 0
            if active_count >= PRODUCT_CATALOG_CAP:
                raise HTTPException(
                    status_code=400,
                    detail=f"Product catalog cap reached ({active_count}/{PRODUCT_CATALOG_CAP}). "
                           f"Deactivate existing products before adding new ones.",
                )

            # Map AI product types to scoring-compatible types
            type_map = {
                "investment": "investment",
                "savings": "savings",
                "lending": "personal_loan",
                "mortgage": "mortgage",
                "credit_card": "credit_card",
                "insurance": "insurance",
            }
            scoring_type = type_map.get(product_type, product_type)
            is_credit = scoring_type in ("personal_loan", "mortgage", "credit_card")

            # Generate a product_id from the name
            product_id = "prod_ai_" + product_name.lower().replace(" ", "_").replace("—", "").replace("(", "").replace(")", "").replace("%", "pct").replace(".", "")[:60]

            # Check if already in catalog
            exists = await session.execute(
                text("SELECT product_id FROM products WHERE product_id = :pid OR product_name = :pname"),
                {"pid": product_id, "pname": product_name},
            )
            if exists.fetchone():
                await session.execute(
                    text("UPDATE ai_product_suggestions SET status = 'implemented' WHERE id = :sid"),
                    {"sid": suggestion_id},
                )
                await session.commit()
                return {"id": suggestion_id, "product_id": product_id, "status": "implemented", "message": "Product already in catalog"}

            # ── Guardrail #7: 24h cool-down staging ──
            # Insert with active=FALSE and go_live_at timestamp
            go_live_at = datetime.utcnow() + timedelta(hours=COOLDOWN_HOURS)

            await session.execute(
                text("""INSERT INTO products
                    (product_name, product_id, type, risk_level, short_description,
                     category, lifecycle_stage, when_to_recommend, is_credit_product, active)
                    VALUES (:name, :pid, :type, :risk, :desc, :cat, :lifecycle, :recommend, :credit, FALSE)"""),
                {
                    "name": product_name,
                    "pid": product_id,
                    "type": scoring_type,
                    "risk": risk_level,
                    "desc": description,
                    "cat": product_type,
                    "lifecycle": ", ".join(target_segments) if target_segments else "all",
                    "recommend": f"AI-suggested. Segments: {', '.join(target_segments)}. Risk: {risk_level}. "
                                 f"Staging until {go_live_at.strftime('%Y-%m-%d %H:%M UTC')}.",
                    "credit": is_credit,
                },
            )

            # Mark suggestion as implemented
            await session.execute(
                text("""UPDATE ai_product_suggestions
                        SET status = 'implemented', approved_by = :by, approved_at = NOW()
                        WHERE id = :sid"""),
                {"sid": suggestion_id, "by": implemented_by},
            )
            await session.commit()

            resp = {
                "id": suggestion_id,
                "product_id": product_id,
                "product_name": product_name,
                "product_type": scoring_type,
                "risk_level": risk_level,
                "is_credit_product": is_credit,
                "status": "implemented",
                "active": False,
                "go_live_at": go_live_at.isoformat(),
                "message": f"Product in {COOLDOWN_HOURS}h staging — goes live {go_live_at.strftime('%Y-%m-%d %H:%M UTC')}. "
                           f"Compliance team can review before activation.",
            }
            try:
                await write_audit_log(
                    request, action="implement", resource_type="ai_suggestion",
                    resource_id=str(suggestion_id),
                    actor=implemented_by, actor_type="staff",
                    changes={"product_id": product_id, "product_name": product_name,
                             "risk_level": risk_level, "go_live_at": go_live_at.isoformat(),
                             "original_approver": original_approver},
                )
            except Exception:
                pass
            return resp
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Implement error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to implement suggestion: " + str(e))


@router.post(
    "/activate-staged",
    summary="Activate AI products that have passed their cool-down staging period",
)
async def activate_staged_products(request: Request):
    """Guardrail #7: Activate products whose cool-down has elapsed. Called periodically or manually."""
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            # Find AI products still inactive whose staging period has passed
            # Products created > COOLDOWN_HOURS ago that are still inactive
            result = await session.execute(
                text("""UPDATE products
                        SET active = TRUE, updated_at = NOW()
                        WHERE product_id LIKE 'prod_ai_%%'
                          AND active = FALSE
                          AND updated_at < NOW() - INTERVAL '1 hour' * :hours
                        RETURNING product_id, product_name"""),
                {"hours": COOLDOWN_HOURS},
            )
            activated = [{"product_id": r[0], "product_name": r[1]} for r in result.fetchall()]
            await session.commit()
            if activated:
                logger.info("Cool-down complete: activated %d AI products: %s",
                            len(activated), [a["product_id"] for a in activated])
            return {"activated": activated, "count": len(activated)}
    except Exception as e:
        logger.error("Activate staged error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to activate staged products")


@router.get(
    "/guardrails",
    summary="Get current guardrail configuration",
)
async def get_guardrails():
    """Return the active guardrail settings for transparency (AI Act Art. 13)."""
    return {
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "rate_limit_per_hour": RATE_LIMIT_MAX_CALLS,
        "stale_expiry_days": STALE_SUGGESTION_DAYS,
        "product_catalog_cap": PRODUCT_CATALOG_CAP,
        "cooldown_staging_hours": COOLDOWN_HOURS,
        "content_filter": "active (PII redaction, prompt injection detection)",
        "dual_approval": "required for high-risk products",
        "audit_log_immutability": "PostgreSQL trigger prevents UPDATE/DELETE on audit_recommendations",
        "fairness_monitoring": "Prometheus histograms by age bracket and income tier",
        "ai_api_tracking": "success/failure/latency metrics per provider",
    }


# ════════════════════════════════════════════════════════════════
# AI-powered Playbook Analysis
# ════════════════════════════════════════════════════════════════

_PLAYBOOK_SYSTEM_PROMPT = """You are a senior banking product performance analyst.
You receive product KPI metrics and return a structured JSON analysis with concrete, actionable recommendations.

Respond ONLY with valid JSON — no markdown, no text outside the JSON object.

Return this exact structure:
{
  "summary": "2-3 sentence overall portfolio assessment with specific numbers",
  "recommendations": [
    {
      "product_name": "exact product name from the input data",
      "situation": "One sentence describing the specific problem, with actual numbers from the data",
      "root_cause": "Most likely root cause based on the metrics pattern",
      "action": "Specific, concrete recommended action the product team can act on",
      "priority": "high|medium|low"
    }
  ]
}

Include only products that need attention. Order by priority (high first).
Be specific — quote actual numbers. Do not include generic advice."""


async def _call_provider_for_playbook(provider: str, config: dict, system_prompt: str, user_prompt: str) -> str | None:
    """Call an AI provider with custom prompts for playbook analysis. Returns raw text or None."""
    api_key   = config.get("api_key", "").strip()
    api_token = config.get("api_token", "").strip()
    base_url  = config.get("base_url", "").strip().rstrip("/")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(read=60.0, connect=10.0, write=10.0, pool=5.0)
        ) as client:
            if provider == "Anthropic":
                if not api_key:
                    return None
                model = config.get("model", "claude-sonnet-4-6")
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": model, "max_tokens": 2048,
                          "system": system_prompt,
                          "messages": [{"role": "user", "content": user_prompt}]},
                )
                resp.raise_for_status()
                return resp.json()["content"][0]["text"]

            elif provider == "OpenAI":
                if not api_key:
                    return None
                model = config.get("model", "gpt-4o")
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "max_tokens": 2048,
                          "response_format": {"type": "json_object"},
                          "messages": [{"role": "system", "content": system_prompt},
                                       {"role": "user",   "content": user_prompt}]},
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]

            elif provider == "Perplexity":
                if not api_key:
                    return None
                model = config.get("model", "sonar-pro")
                resp = await client.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "max_tokens": 2048,
                          "messages": [{"role": "system", "content": system_prompt},
                                       {"role": "user",   "content": user_prompt}]},
                )
                resp.raise_for_status()
                text_val = resp.json()["choices"][0]["message"]["content"].strip()
                if text_val.startswith("```"):
                    text_val = text_val.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                return text_val

            elif provider == "Google":
                if not api_key:
                    return None
                model = config.get("model", "gemini-2.0-flash")
                resp = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
                    headers={"Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": system_prompt + "\n\n" + user_prompt}]}],
                          "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048,
                                               "responseMimeType": "application/json"}},
                )
                resp.raise_for_status()
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

            elif provider == "Hugging Face":
                if not api_token:
                    return None
                model_id = config.get("model_id", "mistralai/Mixtral-8x7B-Instruct-v0.1")
                resp = await client.post(
                    f"https://api-inference.huggingface.co/models/{model_id}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
                    json={"model": model_id, "max_tokens": 2048,
                          "messages": [{"role": "system", "content": system_prompt},
                                       {"role": "user",   "content": user_prompt}]},
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]

            elif provider == "Local LLM":
                if not base_url:
                    return None
                ak = api_key or "not-needed"
                model = config.get("model", "llama3")
                resp = await client.post(
                    f"{base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {ak}", "Content-Type": "application/json"},
                    json={"model": model, "max_tokens": 2048,
                          "messages": [{"role": "system", "content": system_prompt},
                                       {"role": "user",   "content": user_prompt}]},
                )
                resp.raise_for_status()
                text_val = resp.json()["choices"][0]["message"]["content"].strip()
                if text_val.startswith("```"):
                    text_val = text_val.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                return text_val

    except Exception as e:
        logger.error("Playbook AI call (%s) error: %s", provider, e)
    return None


@router.get(
    "/playbook-analysis",
    summary="AI-powered product performance playbook analysis",
)
async def get_playbook_analysis(
    request: Request,
    days: int = Query(30, ge=7, le=90),
):
    """Fetch product performance metrics from DB and analyse them with the first
    available active AI connector. Returns per-product recommendations + portfolio summary."""
    session_factory = request.app.state.db_session_factory

    async with session_factory() as session:
        # Load active AI connectors
        conn_result = await session.execute(
            text("""SELECT name, provider, config_values
                    FROM connectors
                    WHERE category = 'ai' AND status = 'active'
                    ORDER BY name""")
        )
        connectors = [
            {"name": r[0], "provider": r[1],
             "config": r[2] if isinstance(r[2], dict) else json.loads(r[2]) if r[2] else {}}
            for r in conn_result.fetchall()
        ]

        # Fetch product KPIs (simplified — just what AI needs for analysis)
        days_str = str(days)
        perf_result = await session.execute(
            text("""
                WITH offers_gen AS (
                    SELECT offer->>'product_name' AS product_name,
                           COUNT(*) AS offers_generated
                    FROM audit_recommendations,
                         jsonb_array_elements(final_offers) AS offer
                    WHERE created_at >= NOW() - (:days || ' days')::INTERVAL
                    GROUP BY 1
                ),
                excluded AS (
                    SELECT exc->>'product_name' AS product_name,
                           COUNT(*) AS blocked_count
                    FROM audit_recommendations,
                         jsonb_array_elements(
                             CASE WHEN jsonb_array_length(excluded_products) > 0
                                  THEN excluded_products ELSE '[]'::jsonb END
                         ) AS exc
                    WHERE created_at >= NOW() - (:days || ' days')::INTERVAL
                    GROUP BY 1
                ),
                notif AS (
                    SELECT product_name,
                           COUNT(*)                                     AS offers_sent,
                           COUNT(*) FILTER (WHERE read = true)          AS offers_opened,
                           COUNT(*) FILTER (WHERE action = 'submitted') AS journey_started
                    FROM notifications
                    WHERE created_at >= NOW() - (:days || ' days')::INTERVAL
                    GROUP BY 1
                ),
                apps AS (
                    SELECT product_name,
                           COUNT(*) FILTER (WHERE status = 'approved') AS activated
                    FROM application_forms
                    WHERE created_at >= NOW() - (:days || ' days')::INTERVAL
                    GROUP BY 1
                )
                SELECT p.product_name, p.category,
                       COALESCE(og.offers_generated, 0)  AS offers_generated,
                       COALESCE(ex.blocked_count, 0)      AS blocked,
                       ROUND(CASE WHEN COALESCE(og.offers_generated, 0) > 0
                             THEN (COALESCE(og.offers_generated,0) - COALESCE(ex.blocked_count,0)) * 100.0
                                  / og.offers_generated
                             ELSE 0 END, 1)               AS compliance_pass_rate,
                       COALESCE(no.offers_sent, 0)        AS sent,
                       COALESCE(no.offers_opened, 0)      AS opened,
                       COALESCE(no.journey_started, 0)    AS journey_started,
                       COALESCE(ap.activated, 0)          AS activated
                FROM products p
                LEFT JOIN offers_gen og ON og.product_name = p.product_name
                LEFT JOIN excluded   ex ON ex.product_name = p.product_name
                LEFT JOIN notif      no ON no.product_name = p.product_name
                LEFT JOIN apps       ap ON ap.product_name = p.product_name
                WHERE p.active = true
                ORDER BY COALESCE(og.offers_generated, 0) DESC
            """),
            {"days": days_str},
        )
        products = [
            {
                "product_name":         r[0],
                "category":             r[1],
                "offers_generated":     int(r[2]),
                "blocked":              int(r[3]),
                "compliance_pass_rate": float(r[4]),
                "sent":                 int(r[5]),
                "opened":               int(r[6]),
                "journey_started":      int(r[7]),
                "activated":            int(r[8]),
            }
            for r in perf_result.fetchall()
        ]

    if not products:
        raise HTTPException(status_code=404, detail="No product data for this period")

    # Build compact data table for the AI prompt
    table_lines = [
        "Product | Category | Generated | Blocked | Compliance% | Sent | Opened | Journey | Activated",
        "-" * 95,
    ]
    for p in products:
        table_lines.append(
            f"{p['product_name']} | {p['category']} | {p['offers_generated']} "
            f"| {p['blocked']} | {p['compliance_pass_rate']}% "
            f"| {p['sent']} | {p['opened']} | {p['journey_started']} | {p['activated']}"
        )
    data_table = "\n".join(table_lines)

    user_prompt = (
        f"Analyse the following banking product performance data for the last {days} days "
        f"and provide specific, actionable recommendations.\n\n"
        f"{data_table}\n\n"
        f"Focus on:\n"
        f"- Products with compliance pass rate below 80% (many offers blocked)\n"
        f"- Products with high generation but zero activations\n"
        f"- Products with a good funnel drop-off (generated→sent→opened gap)\n"
        f"- Products that are performing well (brief positive note)\n\n"
        f"Return your analysis as JSON per the specified format."
    )

    # Try each connector in order until one succeeds
    ai_text = None
    connector_used = None
    for conn in connectors:
        has_cred = any(
            conn["config"].get(k, "").strip()
            for k in ("api_key", "api_token", "base_url")
        )
        if not has_cred:
            logger.info("Playbook: skipping %s — no credentials", conn["name"])
            continue
        logger.info("Playbook analysis: calling %s (%s)", conn["name"], conn["provider"])
        ai_text = await _call_provider_for_playbook(
            conn["provider"], conn["config"], _PLAYBOOK_SYSTEM_PROMPT, user_prompt
        )
        if ai_text:
            connector_used = conn
            logger.info("Playbook: got response from %s", conn["name"])
            break
        logger.warning("Playbook: %s returned nothing, trying next", conn["name"])

    if not ai_text or not connector_used:
        raise HTTPException(status_code=503, detail="No active AI connector available or all calls failed")

    # Parse AI JSON
    try:
        ai_text = ai_text.strip()
        if ai_text.startswith("```"):
            ai_text = ai_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = json.loads(ai_text)
    except Exception:
        logger.error("Playbook: AI returned non-JSON: %s", ai_text[:200])
        raise HTTPException(status_code=502, detail="AI returned unparseable response")

    # Sanitize output
    valid_priorities = {"high", "medium", "low"}
    recommendations = []
    for r in parsed.get("recommendations", []):
        situation, _ = _sanitize_ai_text(str(r.get("situation", "")))
        root_cause, _ = _sanitize_ai_text(str(r.get("root_cause", "")))
        action, _     = _sanitize_ai_text(str(r.get("action", "")))
        recommendations.append({
            "product_name": str(r.get("product_name", ""))[:200],
            "situation":    situation,
            "root_cause":   root_cause,
            "action":       action,
            "priority":     r.get("priority", "medium") if r.get("priority") in valid_priorities else "medium",
        })

    summary, _ = _sanitize_ai_text(str(parsed.get("summary", "")))

    return {
        "connector":       connector_used["name"],
        "provider":        connector_used["provider"],
        "generated_at":    datetime.utcnow().isoformat() + "Z",
        "days":            days,
        "summary":         summary,
        "recommendations": recommendations,
    }


@router.delete(
    "/suggestions/{suggestion_id}",
    summary="Delete a product suggestion",
)
async def delete_suggestion(suggestion_id: int, request: Request):
    session_factory = request.app.state.db_session_factory
    try:
        async with session_factory() as session:
            result = await session.execute(
                text("DELETE FROM ai_product_suggestions WHERE id = :sid RETURNING id"),
                {"sid": suggestion_id},
            )
            if not result.fetchone():
                raise HTTPException(status_code=404, detail="Suggestion not found")
            await session.commit()
            try:
                await write_audit_log(
                    request, action="delete", resource_type="ai_suggestion",
                    resource_id=str(suggestion_id),
                    changes={"deleted": True},
                )
            except Exception:
                pass
            return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Delete error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to delete suggestion")
