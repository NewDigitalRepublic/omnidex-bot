import os
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import schwab
from schwab.orders.equities import equity_buy_market, equity_sell_market
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="OMNIDEX Bot", version="3.2")

# ── Schwab credentials ──────────────────────────────────────────────────────
API_KEY       = os.environ["SCHWAB_API_KEY"]
SECRET        = os.environ["SCHWAB_SECRET"]
CALLBACK_URL  = os.environ["SCHWAB_CALLBACK_URL"]
ACCOUNT_HASH  = os.environ["ACCOUNT_HASH"]
TQQQ_SHARES   = int(os.environ.get("TQQQ_SHARES", 100))
SQQQ_SHARES   = int(os.environ.get("SQQQ_SHARES", 100))
TOKEN_PATH    = "/tmp/schwab_token.json"

# ── Schwab client (lazy init) ───────────────────────────────────────────────
_client = None

def get_client():
    global _client
    if _client is None:
        _client = schwab.auth.client_from_token_file(
            TOKEN_PATH, API_KEY, SECRET
        )
    return _client

# ── Position tracker ────────────────────────────────────────────────────────
current_position = {"symbol": None, "side": None}

def place_order(symbol: str, action: str, qty: int):
    """Place a market order via Schwab API."""
    client = get_client()
    if action == "BUY":
        order = equity_buy_market(symbol, qty)
    elif action == "SELL":
        order = equity_sell_market(symbol, qty)
    else:
        raise ValueError(f"Unknown action: {action}")
    resp = client.place_order(ACCOUNT_HASH, order)
    resp.raise_for_status()
    logger.info(f"Order placed: {action} {qty} {symbol} | status={resp.status_code}")
    return resp.status_code

def flatten_position():
    """Close any open TQQQ or SQQQ position."""
    global current_position
    sym  = current_position["symbol"]
    side = current_position["side"]
    if sym and side == "LONG":
        place_order(sym, "SELL", TQQQ_SHARES if sym == "TQQQ" else SQQQ_SHARES)
    current_position = {"symbol": None, "side": None}

# ── Webhook endpoint ────────────────────────────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request):
    """
    Expected TrendSpider JSON payload:
    {
      "signal": "LONG_TQQQ" | "LONG_SQQQ" | "FLAT",
      "score":  <float -10 to 10>,
      "ticker": "QQQ"
    }
    """
    global current_position
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    signal = data.get("signal", "").upper()
    score  = float(data.get("score", 0))
    logger.info(f"Webhook received: signal={signal} score={score}")

    if signal == "LONG_TQQQ":
        if current_position["symbol"] != "TQQQ":
            flatten_position()
            place_order("TQQQ", "BUY", TQQQ_SHARES)
            current_position = {"symbol": "TQQQ", "side": "LONG"}
            action_taken = f"Bought {TQQQ_SHARES} TQQQ"
        else:
            action_taken = "Already long TQQQ – no change"

    elif signal == "LONG_SQQQ":
        if current_position["symbol"] != "SQQQ":
            flatten_position()
            place_order("SQQQ", "BUY", SQQQ_SHARES)
            current_position = {"symbol": "SQQQ", "side": "LONG"}
            action_taken = f"Bought {SQQQ_SHARES} SQQQ"
        else:
            action_taken = "Already long SQQQ – no change"

    elif signal == "FLAT":
        flatten_position()
        action_taken = "Flattened all positions"

    else:
        action_taken = f"Unrecognised signal '{signal}' – ignored"
        logger.warning(action_taken)

    return JSONResponse({"status": "ok", "action": action_taken, "score": score})

# ── Health check ─────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "OMNIDEX bot online", "version": "3.2"}

# ── OAuth callback (one-time token exchange) ──────────────────────────────
@app.get("/oauth/callback")
async def oauth_callback(code: str, session: str = ""):
    """Handles Schwab OAuth redirect to mint the initial token file."""
    client = schwab.auth.client_from_login_flow(
        API_KEY, SECRET, CALLBACK_URL, TOKEN_PATH,
        asyncio=False
    )
    return {"status": "Token saved. OMNIDEX is authorised with Schwab."}
