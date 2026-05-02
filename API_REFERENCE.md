# API Reference — Prediction Market Trading System

Quick-reference for all external APIs used by the system. Updated 2026-04-30.

---

## 1. Polymarket

### Architecture
Three separate APIs:
| API | Base URL | Auth | Purpose |
|-----|----------|------|---------|
| **Gamma** | `https://gamma-api.polymarket.com` | None | Market discovery, metadata, events |
| **CLOB** | `https://clob.polymarket.com` | Wallet-derived | Order book, trading |
| **Data** | `https://data-api.polymarket.com` | None | User positions, activity |

### Gamma API — Market Discovery
```
GET /events?active=true&closed=false&order=volume24hr&ascending=false&limit=100
GET /markets?active=true&limit=100
```
**Key fields:**
- `events[].markets[]` — individual tradable markets
- `markets[].clobTokenIds[]` — YES token = index 0, NO token = index 1
- `markets[].outcomePrices[]` — implied probabilities [YES, NO]
- `markets[].question` — market text
- `markets[].active`, `markets[].closed` — status filters
- `markets[].volume24hr`, `markets[].liquidity` — liquidity metrics

### CLOB API — Trading
```
GET /book?token_id=<token-id>           # public, no auth
GET /prices?token_id=<token-id>         # public
POST /auth/api-key                      # L1 auth (wallet signature)
```

**L1 Authentication (derive API credentials):**
```python
# Sign EIP-712 typed data with wallet
nonce = int(time.time() * 1000)
sig = wallet.signTypedData(domain, types, {"nonce": nonce})
# POST to /auth/api-key with headers:
# POLY_ADDRESS, POLY_SIGNATURE, POLY_NONCE, POLY_TIMESTAMP
# Returns: {apiKey, secret, passphrase}
```

**L2 Authenticated Trading Headers:**
```
POLY_ADDRESS:     <wallet_address>
POLY_API_KEY:     <apiKey>
POLY_PASSPHRASE:  <passphrase>
POLY_SIGNATURE:   <HMAC_signature>
POLY_TIMESTAMP:   <unix_ms>
```

**Official Python SDK:** `py-clob-client`
```bash
pip install py-clob-client
```

**Critical Notes:**
- No testnet/sandbox. Test with $1 orders on mainnet.
- Must call `setAllowances()` once before trading (USDC + CTF approvals).
- Polygon wallet needs POL gas token (~$0.01–0.10 covers hundreds of tx).
- WebSocket heartbeats required or server cancels all open orders.

### WebSocket Feeds
```
wss://ws-subscriptions-clob.polymarket.com/ws/market   # book + trades
wss://ws-subscriptions-clob.polymarket.com/ws/user     # fills + order status
wss://ws-live-data.polymarket.com                      # institutional RTDS
```

---

## 2. Kalshi

### Base URLs
| Environment | URL |
|-------------|-----|
| Production | `https://trading-api.kalshi.com/trade-api/v2` |
| Sandbox | `https://demo.kalshi.com` (fake money testing) |

### Authentication — Session Token
```python
# Step 1: Login with email/password
POST /login
{"email": "...", "password": "..."}
# Returns: {"token": "..."}

# Step 2: Use token on all subsequent requests
headers = {"Authorization": f"Bearer {token}"}
```

**Alternative:** Some implementations use API key + secret. Check your Kalshi developer dashboard for the exact auth method enabled on your account.

### Key Endpoints
```
GET  /markets                    # list markets (filter: active, event_ticker, etc.)
GET  /markets/{ticker}           # market detail
GET  /markets/{ticker}/orderbook # bids / asks
POST /portfolio/orders           # place order
GET  /portfolio/positions        # open positions
GET  /portfolio/balance          # account balance
```

**Order placement (POST /portfolio/orders):**
```json
{
  "ticker": "KXELON-25",
  "action": "buy",
  "side": "yes",
  "count": 10,
  "type": "limit",
  "price": 65
}
```
Kalshi prices are in cents (0–100). Price 65 = $0.65.

**Critical Notes:**
- Kalshi has a **real sandbox** — use it for all testing.
- As a CFTC-regulated exchange, KYC is required for live trading.
- All endpoints require authentication (unlike Polymarket Gamma).

---

## 3. Metaculus

### Base URL
```
https://www.metaculus.com/api
```

### Authentication
```
Authorization: Token <METACULUS_API_TOKEN>
```
Get token from: https://www.metaculus.com/accounts/settings

### Key Endpoints
```
GET  /api/posts/?statuses=open&forecast_type=binary&with_cp=true&limit=20
GET  /api/posts/{postId}/
POST /api/questions/forecast/
GET  /api/posts/{postId}/download-data/
```

**Community Predictions:**
- Always add `with_cp=true` to list endpoints or CP aggregations are empty.
- Use `include_cp_history=true` for historical time-series (larger response).
- Aggregation methods: `recency_weighted`, `unweighted`, `metaculus_prediction`, `single_aggregation`

**Forecast submission (binary):**
```json
POST /api/questions/forecast/
[{"question": 12345, "probability_yes": 0.63}]
```

**Important:** `question_id != post_id`. Fetch the post first, then extract `post.question.id`.

**Rate Limits:** Throttled — implement exponential backoff on 429.

---

## 4. Good Judgment

### Access Tiers
| Tier | URL | API Access |
|------|-----|------------|
| **GJ Open** | https://www.gjopen.com | No official API. Web scraping possible but discouraged. |
| **FutureFirst** | Enterprise subscription | API downloads of current + historical forecasts. |

**Status for this system:**
- FutureFirst API requires a paid subscription.
- If subscribed, forecasts are downloaded via authenticated API endpoints (contact Good Judgment for spec).
- If not subscribed, GJ Open consensus must be scraped or omitted.

**Recommendation:** Start with Metaculus (free API) as your primary superforecaster signal. Add Good Judgment if budget allows.

---

## 5. Manifold Markets

### Base URL
```
https://api.manifold.markets/v0
```

### Authentication
- **Read:** No auth required.
- **Write:** `Authorization: Key <API_KEY>` from profile settings.

### Key Endpoints
```
GET /markets?limit=100         # paginated market list
GET /market/{marketId}         # market detail + probabilities
GET /market/{marketId}/prob    # current probability
POST /bet                      # place bet (auth required)
```

**Rate Limits:** 500 requests/minute per IP.

**Use in this system:**
- Cross-platform arbitrage signal source (not for capital deployment).
- Play-money market — good for validating signal logic before real-money trades.

---

## 6. NewsAPI (News & Intel Agent)

### Base URL
```
https://newsapi.org/v2
```

### Authentication
```
?apiKey=<NEWS_API_KEY>
```
or header: `X-Api-Key: <key>`

### Key Endpoints
```
GET /everything?q=<query>&from=<date>&sortBy=publishedAt&apiKey=...
GET /top-headlines?category=business&apiKey=...
GET /sources
```

**Rate Limits:**
- Free tier: 100 requests/day
- Paid tiers: up to 1M requests/month

**Notes:**
- Use targeted queries per market category (political, economic, sports).
- `everything` endpoint searches 80,000+ sources.
- Consider Perigon or NewsData.io as paid alternatives for higher volume + sentiment metadata.

---

## Environment Variables Summary

| Variable | Required For | Source |
|----------|--------------|--------|
| `POLYMARKET_PRIVATE_KEY` | Polymarket trading | Wallet private key (Polygon EOA) |
| `KALSHI_API_KEY` | Kalshi data + trading | Kalshi developer dashboard |
| `KALSHI_API_SECRET` | Kalshi auth | Kalshi developer dashboard |
| `METACULUS_API_KEY` | Superforecaster signals | metaculus.com/accounts/settings |
| `NEWS_API_KEY` | News & Intel Agent | newsapi.org |
| `OPENAI_API_KEY` | LLM signal extraction / NLP | openai.com |
| `ANTHROPIC_API_KEY` | Agent LLM calls | anthropic.com |
| `MANIFOLD_API_KEY` | Manifold cross-platform arb | manifold.markets profile |

**Optional / Future:**
| Variable | Required For | Source |
|----------|--------------|--------|
| `GOODJUDGMENT_API_KEY` | Superforecaster signals | Enterprise subscription |
| `PERPLEXITY_API_KEY` | Alternative news search | perplexity.ai |
| `ASKNEWS_CLIENT_ID` | Premium news + sentiment | asknews.app |

---

## Quick Start Checklist

- [ ] Fund Polygon wallet with POL gas + USDC.e for Polymarket
- [ ] Run `setAllowances()` on Polymarket (one-time)
- [ ] Derive Polymarket CLOB API credentials via L1 auth
- [ ] Create Kalshi sandbox account at `demo.kalshi.com`
- [ ] Get Metaculus token and test `GET /api/posts/?with_cp=true`
- [ ] Get NewsAPI key and test `/everything?q=election`
- [ ] Verify `.env` has all required keys for your chosen mode
