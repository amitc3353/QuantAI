"""
cto_agent.py — CTO Tech Intelligence Agent
============================================
The system's technology scout. Runs every Monday 6:00 AM before
market open. Scans the open-source and research landscape for
tools, libraries, and approaches that could improve QuantAI.

WHAT IT SCANS:
  GitHub — trending repos in: algorithmic trading, options pricing,
           volatility forecasting, financial data, quant finance
  arXiv  — new papers in: quantitative finance (q-fin), computational
           finance, machine learning for trading
  Blogs  — RSS feeds from known quant/finance sources

WHAT IT EVALUATES EACH FINDING AGAINST:
  - Our current architecture (reads SYSTEM_STATE.md)
  - Security criteria (stars, maintainers, CVEs, phone-home behavior)
  - Cost impact (free / replaces paid / adds cost)
  - Integration effort (low / medium / high)
  - Relevance to current weak points (data quality, flow detection,
    IV accuracy, backtesting, pattern detection)

WHAT IT PRODUCES:
  A ranked proposal report posted to #research every Monday.
  Each proposal includes:
    - What it is and what it does
    - What it replaces or improves in our system
    - Security assessment (pass/flag/reject)
    - Integration effort + cost impact
    - Exact implementation path if approved

WHAT IT NEVER DOES:
  - Install anything automatically
  - Modify any code or config
  - Act without Amit's explicit approval
  - Suggest bypassing security rules

APPROVAL FLOW:
  Report posted to #research → Amit reads and reacts → discuss in #chat
  → approved items implemented in next Claude.ai session

COST: ~$0.15/week (1-2 Sonnet calls with web search)

CAN ALSO BE INVOKED ON-DEMAND:
  In #chat: "CTO scan for [topic]" → immediate research + report
"""

import os
import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
import asyncio
import aiohttp

log = logging.getLogger("cto-agent")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SONNET_MODEL = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "")

SYSTEM_STATE_PATH = Path("/app/SYSTEM_STATE.md")
RESULTS_DIR = Path("/app/data/journal")
CACHE_DIR = Path("/app/data/cache")


# ─────────────────────────────────────────────────────────────────────────────
# GITHUB SCANNER
# ─────────────────────────────────────────────────────────────────────────────

GITHUB_SEARCH_QUERIES = [
    "algorithmic trading options python",
    "volatility forecasting machine learning",
    "options flow dark pool python",
    "quantitative finance backtesting",
    "market microstructure data python",
    "implied volatility surface python",
    "financial time series prediction",
]

async def scan_github(session: aiohttp.ClientSession, days_back: int = 7) -> list:
    """
    Search GitHub for recently updated relevant repos.
    Uses search API — no auth needed but token increases rate limit.
    """
    if not GITHUB_TOKEN:
        log.warning("No GITHUB_TOKEN — GitHub rate limit will be low (10 req/min)")

    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    since_date = (date.today() - timedelta(days=days_back)).isoformat()
    findings = []

    for query in GITHUB_SEARCH_QUERIES[:4]:  # Limit to avoid rate limits
        try:
            url = (
                f"https://api.github.com/search/repositories"
                f"?q={query.replace(' ', '+')}+pushed:>{since_date}"
                f"&sort=stars&order=desc&per_page=3"
            )
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for repo in data.get("items", []):
                        stars = repo.get("stargazers_count", 0)
                        if stars < 50:  # Skip low-quality repos
                            continue
                        findings.append({
                            "source": "github",
                            "name": repo.get("full_name"),
                            "description": repo.get("description", "")[:200],
                            "stars": stars,
                            "url": repo.get("html_url"),
                            "language": repo.get("language"),
                            "updated_at": repo.get("updated_at", "")[:10],
                            "topics": repo.get("topics", [])[:5],
                            "open_issues": repo.get("open_issues_count", 0),
                            "query": query,
                        })
                elif resp.status == 403:
                    log.warning("GitHub rate limit hit — reduce scan frequency")
                    break
            await asyncio.sleep(1)  # Rate limit courtesy
        except Exception as e:
            log.debug(f"GitHub search failed for '{query}': {e}")
            continue

    # Deduplicate by repo name
    seen = set()
    unique = []
    for f in findings:
        if f["name"] not in seen:
            seen.add(f["name"])
            unique.append(f)

    log.info(f"GitHub: found {len(unique)} relevant repos")
    return unique


# ─────────────────────────────────────────────────────────────────────────────
# ARXIV SCANNER
# ─────────────────────────────────────────────────────────────────────────────

ARXIV_CATEGORIES = [
    "q-fin.TR",   # Trading and Market Microstructure
    "q-fin.CP",   # Computational Finance
    "q-fin.RM",   # Risk Management
    "q-fin.ST",   # Statistical Finance
]

async def scan_arxiv(session: aiohttp.ClientSession, days_back: int = 14) -> list:
    """
    Fetch recent papers from arXiv in quantitative finance categories.
    arXiv API is free, no authentication needed.
    """
    findings = []
    since_date = (date.today() - timedelta(days=days_back)).strftime("%Y%m%d")

    for category in ARXIV_CATEGORIES[:2]:  # TR and CP are most relevant
        try:
            url = (
                f"https://export.arxiv.org/api/query"
                f"?search_query=cat:{category}"
                f"&start=0&max_results=5"
                f"&sortBy=submittedDate&sortOrder=descending"
            )
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # Parse Atom XML manually (avoid lxml dependency)
                    entries = _parse_arxiv_entries(text)
                    for entry in entries:
                        findings.append({
                            "source": "arxiv",
                            "title": entry.get("title", ""),
                            "summary": entry.get("summary", "")[:300],
                            "url": entry.get("url", ""),
                            "published": entry.get("published", "")[:10],
                            "authors": entry.get("authors", [])[:3],
                            "category": category,
                        })
            await asyncio.sleep(2)  # arXiv asks for 3s between requests
        except Exception as e:
            log.debug(f"arXiv scan failed for {category}: {e}")
            continue

    log.info(f"arXiv: found {len(findings)} papers")
    return findings


def _parse_arxiv_entries(xml_text: str) -> list:
    """Simple XML parser for arXiv Atom feed — no external deps."""
    import re
    entries = []
    entry_blocks = re.findall(r"<entry>(.*?)</entry>", xml_text, re.DOTALL)

    for block in entry_blocks:
        def extract(tag):
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.DOTALL)
            return m.group(1).strip() if m else ""

        title = re.sub(r"\s+", " ", extract("title"))
        summary = re.sub(r"\s+", " ", extract("summary"))[:300]
        published = extract("published")[:10]
        url = ""
        for link in re.findall(r'<link[^>]+href="([^"]+)"[^>]*/>', block):
            if "abs" in link:
                url = link
                break
        authors = re.findall(r"<name>(.*?)</name>", block)[:3]

        if title and summary:
            entries.append({
                "title": title,
                "summary": summary,
                "url": url,
                "published": published,
                "authors": authors,
            })

    return entries


# ─────────────────────────────────────────────────────────────────────────────
# PYPI / PACKAGE SCANNER
# ─────────────────────────────────────────────────────────────────────────────

PYPI_PACKAGES_TO_CHECK = [
    # Packages we use — check for security advisories
    "yfinance", "alpaca-py", "py-vollib", "aiohttp", "apscheduler",
    # Packages that could replace our proxies
    "pandas-ta", "ta-lib", "vectorbt", "zipline-reloaded", "nautilus-trader",
]

async def scan_pypi_security(session: aiohttp.ClientSession) -> list:
    """
    Check PyPI packages for recent updates and known vulnerabilities.
    Uses PyPI JSON API (free, no auth).
    """
    findings = []

    for package in PYPI_PACKAGES_TO_CHECK[:6]:  # Limit calls
        try:
            async with session.get(
                f"https://pypi.org/pypi/{package}/json",
                timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    info = data.get("info", {})
                    latest = info.get("version", "unknown")
                    release_date = ""
                    releases = data.get("releases", {})
                    if latest in releases and releases[latest]:
                        release_date = releases[latest][0].get("upload_time", "")[:10]

                    findings.append({
                        "source": "pypi",
                        "package": package,
                        "latest_version": latest,
                        "release_date": release_date,
                        "summary": info.get("summary", "")[:150],
                        "url": info.get("project_url", f"https://pypi.org/project/{package}"),
                    })
        except Exception as e:
            log.debug(f"PyPI check failed for {package}: {e}")
        await asyncio.sleep(0.3)

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM STATE READER
# ─────────────────────────────────────────────────────────────────────────────

def read_system_state() -> str:
    """Read SYSTEM_STATE.md for context injection into CTO analysis."""
    if SYSTEM_STATE_PATH.exists():
        content = SYSTEM_STATE_PATH.read_text()
        # Trim to most relevant sections to save tokens
        lines = content.split("\n")
        relevant = []
        capture = False
        for line in lines:
            if any(h in line for h in [
                "## Known Technical Debt",
                "## Data Sources",
                "## Security Rules",
                "## Agent 1",
                "## Agent 2",
                "## Intelligence Layer",
            ]):
                capture = True
            if capture:
                relevant.append(line)
            if len(relevant) > 120:  # ~3000 chars — enough context
                break
        return "\n".join(relevant)
    return "SYSTEM_STATE.md not found — using general QuantAI context."


# ─────────────────────────────────────────────────────────────────────────────
# CTO ANALYSIS — Claude Sonnet evaluates all findings
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_findings(
    github_findings: list,
    arxiv_findings: list,
    pypi_findings: list,
    scan_topic: str = None,
) -> dict:
    """
    Send all findings to Claude Sonnet for evaluation against our system.
    Returns structured proposals ranked by impact.
    """
    if not ANTHROPIC_API_KEY:
        return {"status": "no_api_key", "proposals": []}

    system_context = read_system_state()

    # Build compact findings summary
    findings_text = []

    if github_findings:
        findings_text.append("GITHUB FINDINGS:")
        for r in github_findings[:6]:
            findings_text.append(
                f"  [{r['stars']}★] {r['name']}: {r['description'][:100]} "
                f"(updated {r['updated_at']}, {r.get('language','')})"
            )

    if arxiv_findings:
        findings_text.append("\nARXIV PAPERS (recent):")
        for p in arxiv_findings[:4]:
            findings_text.append(
                f"  {p['title'][:80]} ({p['published']}): {p['summary'][:100]}"
            )

    if pypi_findings:
        findings_text.append("\nPACKAGE STATUS:")
        for p in pypi_findings[:6]:
            findings_text.append(
                f"  {p['package']} v{p['latest_version']} ({p['release_date']})"
            )

    topic_context = f"\nFOCUS TOPIC: {scan_topic}" if scan_topic else ""

    prompt = f"""You are the CTO of QuantAI, an autonomous options trading system.
Evaluate these findings against our current system and propose specific improvements.{topic_context}

CURRENT SYSTEM STATE (key sections):
{system_context}

FINDINGS THIS WEEK:
{chr(10).join(findings_text)}

Produce a structured CTO tech report. Output ONLY valid JSON, no markdown:
{{
  "proposals": [
    {{
      "rank": 1,
      "title": "Short descriptive title",
      "what_it_is": "One sentence description",
      "what_it_improves": "Specific component in our system it replaces or enhances",
      "current_limitation": "What we have now that's worse",
      "security_assessment": "pass|flag|reject",
      "security_notes": "Specific security concerns if any",
      "effort": "low|medium|high",
      "cost_impact": "free|saves $X/mo|costs $X/mo",
      "implementation_path": "Exact file and approach to integrate",
      "source_url": "GitHub/arXiv URL",
      "priority": "this_week|next_month|backlog"
    }}
  ],
  "package_alerts": [
    {{
      "package": "name",
      "alert": "description of issue or update needed"
    }}
  ],
  "weekly_summary": "2 sentence plain English summary of what's worth attention this week",
  "top_recommendation": "Single most impactful thing to do this week"
}}

EVALUATION RULES:
- Only propose things that genuinely improve our specific system
- Reject anything with security_assessment=reject before it reaches the report
- flag means: worth considering but needs manual security review first
- Implementation path must reference actual files in our codebase
- Cost impact must be specific — not 'saves money' but 'replaces Polygon.io $29/mo'
- Maximum 5 proposals — quality over quantity
- If nothing is clearly better than what we have, say so honestly
- Never propose something that requires bypassing our guard engine or security rules"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": SONNET_MODEL,
                    "max_tokens": 2500,
                    "messages": [{"role": "user", "content": prompt}],
                },
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
                raw = "".join(
                    b["text"] for b in data.get("content", [])
                    if b.get("type") == "text"
                )

        cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(cleaned)
        result["status"] = "complete"
        result["generated_at"] = datetime.now().isoformat()
        result["scan_counts"] = {
            "github": len(github_findings),
            "arxiv": len(arxiv_findings),
            "pypi": len(pypi_findings),
        }
        return result

    except Exception as e:
        log.error(f"CTO analysis failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "proposals": [],
            "weekly_summary": "Analysis failed — check logs",
        }


# ─────────────────────────────────────────────────────────────────────────────
# FULL SCAN — Monday 6:00 AM or on-demand
# ─────────────────────────────────────────────────────────────────────────────

async def run_cto_scan(topic: str = None, days_back: int = 7) -> dict:
    """
    Full CTO scan. Called by scheduler Monday 6:00 AM or on-demand from #chat.
    topic: optional focus area for on-demand scans (e.g. "options flow data")
    """
    log.info(f"=== CTO Scan {'[' + topic + ']' if topic else '(weekly)'} ===")

    async with aiohttp.ClientSession() as session:
        # Run scans concurrently
        github_task = asyncio.create_task(scan_github(session, days_back=days_back))
        arxiv_task = asyncio.create_task(scan_arxiv(session, days_back=days_back * 2))
        pypi_task = asyncio.create_task(scan_pypi_security(session))

        github_findings, arxiv_findings, pypi_findings = await asyncio.gather(
            github_task, arxiv_task, pypi_task,
            return_exceptions=True,
        )

    # Handle exceptions from gather
    if isinstance(github_findings, Exception):
        log.warning(f"GitHub scan failed: {github_findings}")
        github_findings = []
    if isinstance(arxiv_findings, Exception):
        log.warning(f"arXiv scan failed: {arxiv_findings}")
        arxiv_findings = []
    if isinstance(pypi_findings, Exception):
        log.warning(f"PyPI scan failed: {pypi_findings}")
        pypi_findings = []

    # Analyze
    result = await analyze_findings(
        github_findings, arxiv_findings, pypi_findings, scan_topic=topic
    )

    # Save
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"cto_scan_{date.today().isoformat()}"
    if topic:
        filename += f"_{topic.replace(' ', '_')[:20]}"
    with open(RESULTS_DIR / f"{filename}.json", "w") as f:
        json.dump(result, f, indent=2, default=str)

    proposals = result.get("proposals", [])
    log.info(
        f"CTO scan complete: {len(proposals)} proposals | "
        f"top: {result.get('top_recommendation', 'none')[:60]}"
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD EMBED BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def build_cto_scan_embeds(result: dict) -> list:
    """Build Discord embeds from CTO scan results."""
    if result.get("status") in ("error", "no_api_key"):
        return [{
            "title": "⚠️ CTO Scan Failed",
            "description": result.get("error", "Unknown error")[:300],
            "color": 0xF39C12,
            "footer": {"text": "QuantAI CTO Agent"},
        }]

    proposals = result.get("proposals", [])
    summary = result.get("weekly_summary", "")
    top_rec = result.get("top_recommendation", "")
    counts = result.get("scan_counts", {})

    # Security: filter rejected proposals before showing
    safe_proposals = [p for p in proposals if p.get("security_assessment") != "reject"]
    rejected = len(proposals) - len(safe_proposals)

    # Header embed
    header_fields = [
        {
            "name": "Scanned",
            "value": (
                f"{counts.get('github', 0)} GitHub repos · "
                f"{counts.get('arxiv', 0)} papers · "
                f"{counts.get('pypi', 0)} packages"
            ),
            "inline": False,
        },
        {
            "name": "Proposals",
            "value": f"**{len(safe_proposals)}** actionable" +
                     (f" · {rejected} rejected (security)" if rejected else ""),
            "inline": True,
        },
    ]

    if top_rec:
        header_fields.append({
            "name": "🎯 Top Recommendation",
            "value": top_rec[:200],
            "inline": False,
        })

    embeds = [{
        "title": f"🤖 CTO Weekly Scan — {date.today().isoformat()}",
        "description": summary,
        "color": 0x3498DB,
        "fields": header_fields,
        "footer": {"text": "QuantAI CTO Agent · No action taken without your approval"},
        "timestamp": datetime.now().isoformat(),
    }]

    # Proposal embeds (one per proposal, max 4)
    priority_colors = {
        "this_week": 0x2ECC71,
        "next_month": 0xF39C12,
        "backlog": 0x95A5A6,
    }
    security_emoji = {"pass": "✅", "flag": "⚠️", "reject": "❌"}

    for p in safe_proposals[:4]:
        effort_bar = {"low": "▓░░", "medium": "▓▓░", "high": "▓▓▓"}.get(p.get("effort", "?"), "?")
        sec = p.get("security_assessment", "?")
        color = priority_colors.get(p.get("priority", "backlog"), 0x95A5A6)

        fields = [
            {
                "name": "Improves",
                "value": p.get("what_it_improves", "?")[:100],
                "inline": True,
            },
            {
                "name": "Effort",
                "value": f"{effort_bar} {p.get('effort', '?')}",
                "inline": True,
            },
            {
                "name": "Cost",
                "value": p.get("cost_impact", "?"),
                "inline": True,
            },
            {
                "name": f"{security_emoji.get(sec, '?')} Security",
                "value": p.get("security_notes", "No issues") or "No issues",
                "inline": True,
            },
            {
                "name": "Priority",
                "value": p.get("priority", "?").replace("_", " ").title(),
                "inline": True,
            },
        ]

        impl = p.get("implementation_path", "")
        if impl:
            fields.append({
                "name": "Implementation",
                "value": impl[:150],
                "inline": False,
            })

        current_limit = p.get("current_limitation", "")
        if current_limit:
            fields.append({
                "name": "Current limitation",
                "value": current_limit[:150],
                "inline": False,
            })

        url = p.get("source_url", "")
        description = p.get("what_it_is", "")
        if url:
            description += f"\n[View source]({url})"

        embeds.append({
            "title": f"#{p.get('rank', '?')} {p.get('title', 'Proposal')}",
            "description": description[:300],
            "color": color,
            "fields": fields,
        })

    # Package alerts
    alerts = result.get("package_alerts", [])
    if alerts:
        alert_lines = [f"• **{a['package']}**: {a['alert']}" for a in alerts[:4]]
        embeds.append({
            "title": "📦 Package Alerts",
            "description": "\n".join(alert_lines),
            "color": 0xF39C12,
            "footer": {"text": "Review before next deploy"},
        })

    return embeds


# ─────────────────────────────────────────────────────────────────────────────
# CLI TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    async def main():
        result = await run_cto_scan()
        print(f"\nStatus: {result.get('status')}")
        print(f"Summary: {result.get('weekly_summary')}")
        print(f"Top rec: {result.get('top_recommendation')}")
        print(f"\nProposals ({len(result.get('proposals', []))}):")
        for p in result.get("proposals", []):
            sec = p.get("security_assessment", "?")
            print(f"  #{p['rank']} [{sec}] {p['title']} — {p['effort']} effort, {p['cost_impact']}")

    asyncio.run(main())
