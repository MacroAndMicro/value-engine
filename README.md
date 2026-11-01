# Value Engine: Indian Stock Value-Investing Screener

Fetch 10+ years of financial statements for an NSE/BSE stock, compute core
value-investing metrics, judge the business (Graham / Buffett / Fisher / earnings
quality, plus bank rules for lenders), and then judge the price (valuation).

* **Phase 1**: data ingestion, 10-year metrics, scorecard
* **Phase 1.5**: valuation (P/E and P/B vs history, Graham Number, reverse DCF),
  exact net current assets, earnings-quality and bank rules, watchlist + CSV
* **Phase 2**: Claude reads the annual reports (latest one or last 3) for red flags,
  every quoted piece of evidence is verified, and a BUY / WATCHLIST / AVOID memo is written

## Web app (Streamlit)
```powershell
.venv\Scripts\streamlit run app.py
```
Keep that terminal open while you use the app, and open **http://127.0.0.1:8501**. The page only loads while the command is running. Press Ctrl+C in the terminal to stop it.

| Page | What it does |
|---|---|
| **Analyze** | Scorecard, 10-year charts (hover for values), valuation, and a reverse-DCF slider playground |
| **AI Research** | Pick a provider, prepare the annual reports (free), see the cost, then run the review: verdict, verified red flags and the memo, which you can download |
| **Watchlist** | Paste or upload tickers, rank them, chart quality vs price, export CSV. Click a row to open that stock |
| **Learn** | This README's glossary, inside the app |

The sidebar has the same options as the CLI flags: statements, live prices, refresh data.
`.streamlit/config.toml` keeps the app reachable from this computer only, because the AI page can
spend API credit.

### Sharing it online (Streamlit Community Cloud, free)
1. Push this repo to GitHub. A **private** repo is recommended: `tests/fixtures/` contains copies
   of Screener pages.
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub, choose **Create app**,
   then pick the repo, branch `main` and main file `app.py`.
3. Under **Advanced settings**, choose Python 3.12 and paste your secrets (TOML format). Include only
   the keys you use:
   ```toml
   AI_PAGE_PASSWORD = "a long passphrase only you know"
   GEMINI_API_KEY = "..."
   # ANTHROPIC_API_KEY = "sk-ant-..."
   ```
4. Deploy. You get a link like `https://your-app.streamlit.app` to share.

On the hosted site, **Analyze, Watchlist and Learn are open to everyone with the link**. **AI
Research asks for `AI_PAGE_PASSWORD`**, because it spends your API credit. Without that secret the
page stays switched off. On your own computer the page needs no password.

Things that differ online:
- The cache (SQLite, downloaded reports) is wiped whenever the app restarts.
- The app goes to sleep after a period with no visitors and takes a moment to wake up.
- Screener or Yahoo may rate-limit cloud servers. If live prices fail, the app falls back to
  Screener's price.
- Fetching Screener data for *other people* through a public app goes beyond personal use. Check
  Screener's terms before sharing widely.

When you clone this repo on a new computer, recreate `.streamlit/config.toml` (it is not in git)
with `[server]` and `address = "127.0.0.1"`, so the local app isn't visible on your network.

## Phase 2: annual-report review with Claude
```powershell
.venv\Scripts\python -m value_engine TCS.NS --ai                 # latest annual report
.venv\Scripts\python -m value_engine TCS.NS --ai --reports 3     # last 3 (finds auditor changes etc.)
.venv\Scripts\python -m value_engine TCS.NS --ai --yes           # skip the cost confirmation
```
### Choosing an AI provider
You need a key **only for the providers you use**, and one is enough. Each key goes in **its own**
environment variable. A Google key in `ANTHROPIC_API_KEY` will not work, because each provider
only accepts its own keys.

| `--provider` | Key variable | Default model | ~Cost per report* | Notes |
|---|---|---|---|---|
| `anthropic` (default) | `ANTHROPIC_API_KEY` | `claude-opus-5-5` | $0.45 | Native structured output |
| `openai` | `OPENAI_API_KEY` | `gpt-6.1-sol` | $0.22 | Native structured output |
| `google` | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | `gemini-3.8-flash` | $0.09 | Has a **free tier** with daily limits. Google may use free-tier data to improve its models |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-v4-pro` | $0.08 | JSON mode only (validated + 1 retry). Off-peak hours cost half |
| `openai-compatible` | `OPENAI_COMPATIBLE_API_KEY` + `OPENAI_COMPATIBLE_BASE_URL` | pass `--model` | unknown | Mistral, Groq, OpenRouter, local Ollama, ... |

\*One report of about 36k tokens plus the memo, at list prices checked on 2026-11-01. The exact
estimate is shown before anything is sent.

```powershell
.venv\Scripts\python -m value_engine TCS.NS --ai --provider google
.venv\Scripts\python -m value_engine TCS.NS --ai --provider openai --model gpt-6.1-terra
.venv\Scripts\python -m value_engine TCS.NS --ai --provider openai-compatible --model mistral-large-latest
```
To change the default provider, set `AI_PROVIDER` in `config.py`. To add or update a model's price,
edit `AI_PRICES`. A model with no price still works; its cost just shows as "unknown".

**Setup:** add the key as a *user* environment variable (Start > "Edit environment variables for
your account" > New), add credit with that provider if it has no free tier, and restart VS Code.

**Cost:** the estimate is shown before anything is sent. Results are cached in SQLite per provider
and model, so re-running the same report with the same model is free, and comparing models only
costs the new ones.

**Same safeguards for every provider:** every quoted piece of evidence is checked against the report
whichever model wrote it, and the verdict ceiling comes from the same hard rules. A weaker model can
produce fewer or vaguer findings, but it can't push an unsupported red flag into the verdict.

**How it works: the math decides, the AI explains.**
1. The annual-report PDFs are downloaded from BSE (links on the Screener page) into
   `data/reports/SYMBOL/`, and their text is extracted once.
2. `reports/sections.py` picks the pages that matter for the same statement basis the numbers
   use: the auditor's report (with its CARO and internal-controls annexures), notes on contingent
   liabilities, related parties and accounting policies, MD&A, auditor changes, and NPA/CASA pages
   for banks. A 360-page report becomes about 40 pages.
3. Claude returns structured findings (`ai/schemas.py`). Every red flag must carry a verbatim
   quote and page number.
4. `ai/verify.py` checks each quote against the real page text. Flags whose quote can't be found
   are shown as "NOT FOUND - ignored" and never affect the verdict.
5. Hard checks set a **verdict ceiling**:
   * a modified audit opinion, going-concern doubt, a verified critical flag or a WEAK screen means **AVOID**
   * a verified high flag, weak internal controls, contingent liabilities above 10% of net worth,
     an auditor change, or a screen other than QUALITY + VALUE means at most **WATCHLIST**
6. Claude writes the memo. Its verdict can be more cautious than the ceiling, never less.
   The memo is saved to `data/memos/SYMBOL-DATE.md`.

## Setup
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e .
```

## Usage
```powershell
.venv\Scripts\python -m value_engine TCS.NS
.venv\Scripts\python -m value_engine RELIANCE.NS --refresh     # ignore 7-day cache
.venv\Scripts\python -m value_engine 500325.BO --standalone    # BSE code, standalone statements
.venv\Scripts\python -m value_engine INFY.NS --no-quote        # skip yfinance (Screener price, no P/E history)
.venv\Scripts\python -m value_engine --watchlist watchlist.example.txt --csv data\watchlist.csv
.venv\Scripts\python -m pytest                                 # offline tests
```

### Reading the result
* **Business quality x/y**: how many quality rules passed (Graham, Buffett, Fisher,
  earnings quality, and Bank rules for lenders).
* **Valuation a/b**: how many price rules passed.
* **Screen** combines the two:
  * **QUALITY + VALUE**: at least 80% of quality rules pass and at least 60% of valuation rules pass
  * **QUALITY, EXPENSIVE**: good business, but the price assumes a lot
  * **MIXED**: 60–80% of quality rules pass
  * **WEAK**: below 60%

  This is a provisional label. Phase 2 adds annual-report red flags before giving a verdict.
The 10-year table is about 125 characters wide, so widen the terminal if columns get squeezed.

## How it works
```
ticker -> ingestion/screener.py  (Screener.in page + line-item breakdown API, cached in SQLite)
       -> ingestion/normalize.py (clean numbers, drop TTM/half-year columns, canonical names)
       -> storage/db.py          (data/value_engine.db: raw pages + line_items table)
       -> engine/metrics.py      (ROCE, ROE, FCF, D/E, ICR, divergence ...)
       -> engine/valuation.py    (P/E & P/B history, Graham Number, reverse DCF)
       -> engine/rules.py        (quality + valuation pass-fail; thresholds in config.py)
       -> report/console.py      (rich tables) / report/summary.py (watchlist rows, CSV)
pipeline.py ties these together: analyze(ticker) -> Analysis
research.py (Phase 2): reports/fetch.py -> reports/sections.py -> ai/client.py (Analyst)
       -> ai/providers.py (Anthropic / OpenAI / Gemini / DeepSeek / OpenAI-compatible)
       -> ai/verify.py -> hard checks + verdict ceiling -> memo -> report/research_view.py
```
* Data source: Screener.in, consolidated statements first, falling back to standalone if
  consolidated has fewer than 7 years. Price and market cap come from yfinance.
* Units: Rs Crores (1 Crore = 10 million).
* Missing values stay blank (NaN); they are never treated as 0. A rule with fewer than 7 years
  of data says **NO DATA** rather than FAIL.
* Banks and NBFCs: debt-, cash-flow- and margin-based rules show **N/A**, because debt is
  their raw material. Bank-specific metrics (NIM, NPA, CASA) come in a later phase.

## Glossary (what each number means and why it matters)
| Term | Formula | Why a value investor cares |
|---|---|---|
| **EBIT** | Profit before tax + Interest | Operating earnings before financing decisions |
| **Capital Employed** | Equity + Reserves + Borrowings | All the money the business has to work with |
| **ROCE** | EBIT / avg Capital Employed | Return on *all* capital. >15% for 10 years suggests a moat (Buffett) |
| **ROE** | Net Profit / avg Shareholders' Equity | Return to owners. Can be inflated by debt, so read it with D/E |
| **CFO** | Cash from Operating Activity | Cash the business actually collected |
| **CapEx** | Fixed assets purchased | Reinvestment needed to stay in business |
| **Free Cash Flow** | CFO - CapEx | Cash left for owners: dividends, buybacks, growth |
| **Debt / Equity** | Borrowings / Shareholders' Equity | Leverage. Low debt means survival in bad years |
| **Interest Coverage** | EBIT / Interest | How many times profits cover interest. <3x is fragile (Graham) |
| **Net Current Assets** | Other Assets - Other Liabilities (approx.) | Graham wanted debt covered by liquid assets |
| **CFO / Net Profit** | CFO / Net Profit | ~1.0 or above means profits are real cash. Persistently low can signal aggressive accounting |
| **Divergence year** | Profit up **and** CFO down | Early warning: receivables or inventory piling up (Fisher) |
| **Margin trend** | Slope of Gross (or Operating) Margin per year | Rising margins mean pricing power. Falling margins mean competition |
| **CV (volatility)** | Std dev / mean | <0.25 means returns are steady, not lucky |

### Earnings quality
| Check | Rule | What it catches |
|---|---|---|
| Debtor days | latest <= 1.3x the 10-year median (anything <= 30 days passes) | Sales "booked" but not collected. A classic sign of aggressive revenue recognition |
| Other income share | <= 25% of profit before tax | Profit coming from treasury income or one-off gains instead of the business |
| Tax rate | median of the last 5 years >= 15% | Profits that aren't fully taxed: tax holidays ending, or profits that may not be real |
| Per-share growth | EPS CAGR >= profit CAGR - 2 pp | New share issues or minority holders taking the growth away from you |

### Banks and NBFCs
| Metric | Formula | Why |
|---|---|---|
| ROA | Net profit / avg total assets | A bank is levered about 10x, so ROA is the true efficiency test. >= 1% is good |
| NII / Assets | (Interest earned - interest paid) / avg assets | Lending spread. Falling means pricing pressure or costlier funding |
| Deposit growth | 10-year CAGR | A cheap, sticky deposit base is a bank's moat |

Gross/net NPA, CASA ratio and provision coverage aren't in Screener's tables. Phase 2 reads them
from the annual report.

### Valuation
| Measure | Formula | How to read it |
|---|---|---|
| **P/E** | Price / trailing-12-month EPS | Years of today's profit you are paying for |
| **10-yr median P/E** | Median of (FY-end price / EPS) | The stock's "normal" price. Below it means cheaper than usual (not necessarily cheap) |
| **Earnings yield** | 1 / P/E | Compare with a fixed deposit (~7%). Equity should eventually earn more |
| **P/B** | Price / book value per share | Banks are judged on this instead of P/E |
| **Graham Number** | sqrt(22.5 x EPS x book value per share) | Graham's maximum price (P/E 15 x P/B 1.5). Harsh on high-ROE firms by design |
| **FCF yield** | Avg FCF (3 yrs) / market cap | Cash return if the company paid out all free cash |
| **Reverse DCF** | Growth `g` at which discounted future FCF equals today's enterprise value (12% discount rate, 10 years, then 5%) | Instead of guessing the future, it asks what growth the price already assumes. If that's above what the company has delivered over 10 years, you are paying for hope |

## Known approximations (shown as footnotes in the report)
* **Net current assets** = (inventories + receivables + cash + loans & advances) minus
  (payables + customer advances + other liabilities), from Screener's balance-sheet breakdown.
  The asset side counts only clearly-current items, so the figure is conservative.
  Minority interest is excluded because it is equity, not a liability. If the breakdown is
  unavailable, it falls back to Other Assets - Other Liabilities (noted in the report).
* **Debt** includes lease liabilities (Ind AS 116). The Graham rule says how much of it is leases.
* **Historical P/E** uses yfinance's split/bonus-adjusted fiscal-year-end close and Screener's
  restated EPS. **Share count** is net profit / EPS, because Screener's "Equity Capital" jumps at
  every bonus issue and can't be used.
* **Gross margin** = 100% - Material Cost %. For services firms (IT, for example) with ~0% material
  cost, Operating Margin is used instead.
* **CapEx** is gross "Fixed assets purchased", not net of asset sales, so FCF can come out lower
  than Screener's own FCF figure. That is the conservative choice.
* Our ROCE uses *average* capital employed, so it can differ from Screener's by 1–3 pp.

Screener.in is scraped politely for personal research (2 s between requests, 7-day cache).
This is a quantitative screen, not investment advice.
