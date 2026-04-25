"""Pure-Python Bull/Bear case templates for debate_chamber.py.
Eliminates 2 Haiku LLM calls per proposal (~80/day at full cadence).
The judge LLM receives the same markdown-bullet format as before.
"""

_STRATEGY_BULL = {
    "iron_condor":     "Range-bound structure collects premium from both sides; underlying needs only to stay within wings",
    "bull_put_spread": "Bullish/neutral bias; full premium kept if underlying closes above short put at expiry",
    "bear_call_spread": "Bearish/neutral bias; full premium kept if underlying closes below short call at expiry",
    "diagonal_spread": "Time-decay harvest: short near-term option decays faster while long-dated leg provides protection",
    "iron_butterfly":  "ATM premium maximised; IV crush amplifies gains if underlying stays near current price",
    "jade_lizard":     "Upside fully uncapped below short call; put spread bounds the downside",
    "calendar_spread": "Near-term theta outpaces long-dated decay while IV differential provides directional cushion",
}

_STRATEGY_BEAR = {
    "iron_condor":     "Requires underlying to stay within both wing pairs; one trending day blows a side",
    "bull_put_spread": "Any sustained downside break past the short put converts premium into a loss",
    "bear_call_spread": "Any sustained upside break past the short call converts premium into a loss",
    "diagonal_spread": "Net debit at risk if underlying moves sharply away from strike before near-term expiry",
    "iron_butterfly":  "ATM strikes leave almost no room; a 1% underlying move can wipe the credit",
    "jade_lizard":     "Strong downward move through put spread wipes premium plus additional loss",
    "calendar_spread": "IV collapse or large underlying gap collapses spread value below entry cost",
}

_VIX_CONTEXT_BULL = {
    "low":            "Low VIX reduces gap risk; orderly price action benefits range-bound structures",
    "normal":         "VIX in normal range: balanced risk/reward environment",
    "elevated":       "Elevated VIX means richer premium — more cushion to absorb small adverse moves",
    "high":           "High VIX: premium at its fattest; maximum income potential if underlying stabilises",
    "contango":       "VIX contango: near-term fear contained, environment supports premium-selling",
    "backwardation":  "VIX backwardation signals active front-month hedging — elevated gap risk",
}

_VIX_CONTEXT_BEAR = {
    "low":            "Low VIX means thin premium — any adverse move quickly erodes the credit",
    "normal":         "Normal VIX: no special cushion; standard gap-move risk applies",
    "elevated":       "Elevated VIX signals market fear; trending moves common in elevated-VIX environments",
    "high":           "High VIX often precedes violent swings that breach defined-risk wings",
    "contango":       "VIX contango can snap to backwardation on a catalyst, spiking realised vol",
    "backwardation":  "VIX backwardation already pricing in stress; actual vol often exceeds implied",
}


def _vix_key(vix_regime: str) -> str:
    r = vix_regime.lower().replace(" ", "_").replace("-", "_")
    for k in ("low", "normal", "elevated", "high", "contango", "backwardation"):
        if k in r:
            return k
    return "normal"


def build_case(side: str, proposal: dict, macro: dict, regime: str, flags: list) -> str:
    """Return a 4-5 bullet markdown case FOR (bull) or AGAINST (bear) the trade.

    Designed as a drop-in replacement for the Haiku calls in debate_chamber.py.
    Output format identical to what the LLM was producing: plain markdown bullets.
    """
    sym          = proposal.get("symbol", "?")
    strategy     = proposal.get("strategy", "unknown")
    credit       = proposal.get("estimated_credit", 0)
    max_loss     = proposal.get("max_loss", abs(credit) * 4 if credit else 0)
    max_loss_pct = proposal.get("max_loss_pct", 2.0)
    pop          = proposal.get("probability_of_profit", 0)
    thesis       = proposal.get("thesis", "")
    invalidation = proposal.get("invalidation", "")
    vix          = macro.get("vix", 18)
    vix_regime   = macro.get("vix_regime", "normal")
    vk           = _vix_key(vix_regime)

    strat_bull   = _STRATEGY_BULL.get(strategy, "Defined-risk structure caps maximum exposure")
    strat_bear   = _STRATEGY_BEAR.get(strategy, "Adverse underlying move works fully against position")
    vix_bull     = _VIX_CONTEXT_BULL.get(vk, _VIX_CONTEXT_BULL["normal"])
    vix_bear     = _VIX_CONTEXT_BEAR.get(vk, _VIX_CONTEXT_BEAR["normal"])

    credit_label = f"${abs(credit):.2f} debit" if credit < 0 else f"${credit:.2f} credit"
    credit_thin  = 0 < credit < 0.50

    flag_texts = [
        f.get("reason", "")
        for f in (flags or [])
        if f.get("level") in ("HIGH", "CRITICAL") and f.get("reason")
    ]

    if side == "bull":
        bullets = [
            f"**{pop}% probability of profit** — 2-in-3 chance of keeping the full {credit_label}",
            f"**Strategy fit**: {strat_bull} on {sym} at VIX {vix:.1f}",
            f"**Volatility environment**: {vix_bull}",
            f"**Defined risk** — max loss capped at {max_loss_pct:.1f}% of account (${max_loss:.2f}); loss cannot compound beyond this",
        ]
        if thesis:
            bullets.append(f"**Trade thesis**: {thesis}")
        else:
            bullets.append(f"**Regime**: {regime.upper()} supports this structure")
    else:
        bullets = [
            f"**Invalidation**: {invalidation or 'See thesis'} — full max loss ${max_loss:.2f} if triggered",
            f"**Structural risk**: {strat_bear} on {sym}",
            f"**Volatility risk**: {vix_bear} (VIX {vix:.1f})",
            f"**{credit_label} collected** is {'dangerously thin cushion' if credit_thin else 'limited cushion'} against a {max_loss_pct:.1f}% adverse move",
        ]
        if flag_texts:
            bullets.append(f"**Active risk flag**: {flag_texts[0]}")
        else:
            bullets.append(
                f"**Max loss ${max_loss:.2f}** wipes multiple days of income target if trade fails completely"
            )

    return "\n".join(f"• {b}" for b in bullets)
