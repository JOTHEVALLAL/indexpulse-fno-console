# Momentum Edge F&O Console

Independent Phase 1 console and rule engine for generating intraday index-based F&O alerts for:

- NIFTY 50
- BANK NIFTY

Signals are generated from the underlying index only. Option side suggestions are derived after a spot signal is actionable; the engine never places orders.

The Streamlit app supports SAMPLE mode, LIVE underlying-data scanning, and read-only option-contract recommendations. It does not send Telegram messages or place orders.

Cloud preview version: `0.4.0-cloud-preview`

Recommended Python version: `3.11`

## Current Scope

- 5-minute primary signal evaluation
- 15-minute trend confirmation input
- VWAP, EMA, opening-range, previous-day, ATR, relative-volume, and India VIX context
- READY / PREPARE / WAIT / AVOID / NO_TRADE signal statuses
- Duplicate-alert suppression by instrument, direction, setup, and session date
- Dashboard, Intraday Setups, Active Trades, and Alert History views
- Intraday setup filters by instrument, status, and setup
- Status-sorted setup table with visual status labels
- Telegram alert preview formatter
- Local JSON alert-history persistence
- Session-state active-trade and watchlist tracker
- Confirmation-gated sample reset, active-trade clear, and history clear controls
- SAMPLE/LIVE data mode selector
- Isolated Zerodha Kite client adapter for underlying index data
- Scanner diagnostics for fetch status, completed candles, freshness, VWAP source, and current errors
- Completed-candle enforcement for 5-minute and 15-minute inputs
- Live-market conversion into the existing rule-engine input model

## Data Modes

SAMPLE mode is deterministic and never presented as live market data.

LIVE mode reads credentials from environment variables:

```powershell
$env:KITE_API_KEY='your_api_key'
$env:KITE_API_SECRET='your_api_secret'
$env:KITE_ACCESS_TOKEN='your_access_token'
```

If authentication, instrument resolution, or candle retrieval fails, the console shows the LIVE error. It does not replace LIVE rows with SAMPLE rows. If a previous valid live snapshot exists, the console can show it as `LIVE CACHED` with actions disabled. Live mode requires Kite API key, API secret, and access token to be configured before scanner calls are attempted. Live mode can create read-only option recommendations for LIVE READY signals, but it does not send Telegram alerts or place orders.

Streamlit Community Cloud can read the same keys from app secrets:

```toml
KITE_API_KEY = "..."
KITE_API_SECRET = "..."
KITE_ACCESS_TOKEN = "..."
KITE_REDIRECT_URL = "..."
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_IDS = ""
APP_ENV = "streamlit_cloud"
DATA_DIR = "data"
```

Telegram keys are reserved for future phases and are not required.

## Deployment Modes

The app detects:

- `LOCAL`
- `STREAMLIT_CLOUD`

Set explicitly if needed:

```bash
MOMENTUM_EDGE_DEPLOYMENT_MODE=STREAMLIT_CLOUD
```

Initial hosted validation should use SAMPLE mode. LIVE mode remains unavailable until valid Kite configuration and daily access-token handling are provided.

The sidebar includes Deployment Diagnostics with application version, deployment mode, data mode, Python/Streamlit import status, writable data-directory status, local persistence file status, Kite configuration status, and Telegram configuration status. Secret values are never displayed.

Local Streamlit Community Cloud filesystem persistence is temporary and should not be treated as durable storage. The diagnostics label this persistence mode as `TEMPORARY LOCAL FILES`. CSV downloads remain available.

## Live Data Foundation

- Spot instruments are resolved centrally for `NIFTY 50`, `BANK NIFTY`, and `INDIA VIX`.
- Nearest unexpired NIFTY and BANKNIFTY monthly futures are resolved dynamically from NFO instruments.
- Resolved instruments must match the expected NSE exchange and trading symbol.
- Futures must be `NFO`, instrument type `FUT`, correct underlying name, and unexpired.
- Spot index candles provide price structure, EMA, ATR, opening range, previous-day levels, entries, stops, and targets.
- Futures candles provide session VWAP, relative volume, and volume confirmation.
- Missing or zero volume rejects VWAP calculation; no fabricated VWAP is produced.
- Raw futures VWAP is converted to a spot-equivalent comparison level by subtracting the current futures-spot basis. Diagnostics still label the VWAP source as the futures trading symbol and preserve raw futures VWAP.
- Scanner freshness states are `FRESH` up to 90 seconds, `DELAYED` from 91 to 180 seconds, and `STALE` above 180 seconds.
- STALE data blocks `READY` signals by downgrading them to `WAIT`.
- Cached scanner snapshots are reused during normal Streamlit reruns, but data age and freshness are recomputed.
- The last successful snapshot is preserved during temporary LIVE failures and surfaced with the failure state.
- Spot/futures completed candles must align on the same timezone-aware 5-minute timestamp. Misalignment blocks actionable READY/PREPARE states.
- Market diagnostics are persisted to `data/market_diagnostics.csv` with duplicate prevention by instrument and completed candle timestamp.
- Signal lifecycle and alert-centre events are persisted to `data/signal_lifecycle.json`.
- Paper outcome records are persisted to `data/signal_outcomes.json`.
- Read-only option recommendations are persisted to `data/option_recommendations.json`.
- Evaluations are deduplicated by `EVAL|<mode>|<session_date>|<instrument>|<completed_candle_timestamp>`.
- Alert events are deduplicated by stable event key and are never emitted for Streamlit reruns.

## Signal Lifecycle

Lifecycle records track `NO_TRADE`, `WAIT`, `PREPARE`, `READY`, `INVALIDATED`, and `EXPIRED`.

Meaningful alert events are generated only for:

- `PREPARE_NEW`
- `READY_DIRECT`
- `READY_UPGRADE`
- `SIGNAL_INVALIDATED`
- `SIGNAL_EXPIRED`
- `DIRECTION_CHANGED`

PREPARE expires after three completed 5-minute candles by default if it does not become READY. Prior-session PREPARE or READY records expire at session rollover.

The Alert Centre is in-app only. It supports filtering, acknowledgement, event detail, and CSV download. It does not send Telegram messages.

## Paper Outcome Tracking

READY signals create paper execution records in `PENDING_ENTRY`; READY is not treated as an executed trade.

Execution states:

- `PENDING_ENTRY`
- `ENTRY_TRIGGERED`
- `TARGET_1_HIT`
- `TARGET_2_HIT`
- `STOP_LOSS_HIT`
- `INVALIDATED_BEFORE_ENTRY`
- `EXPIRED_BEFORE_ENTRY`
- `SESSION_CLOSED`
- `CLOSED_MANUALLY`
- `AMBIGUOUS`

Bullish entries trigger only when a completed underlying candle reaches or exceeds the planned entry. Bearish entries trigger only when a completed underlying candle reaches or falls below the planned entry. Cached, stale, misaligned, or unavailable LIVE data does not advance paper outcomes.

If a completed candle touches both target and stop after entry, the result is marked `AMBIGUOUS` rather than assuming the favourable outcome. Ambiguous records are counted separately in performance metrics.

MFE and MAE are tracked in index points and R-multiples using the initial planned risk. The Performance page reports entry rate, target hit rates, stop rate, invalidation/expiry rates, realised R, MFE/MAE R, ambiguity count, and breakdowns by instrument, setup, confidence, direction, time bucket, and weekday.

## Option Recommendations

Phase 2C adds read-only CE/PE contract discovery for valid LIVE READY signals only.

- Bullish NIFTY 50 maps to NIFTY CE.
- Bearish NIFTY 50 maps to NIFTY PE.
- Bullish BANK NIFTY maps to BANKNIFTY CE.
- Bearish BANK NIFTY maps to BANKNIFTY PE.

Option contracts are discovered from the Zerodha NFO instrument master. The selector validates exchange, instrument type, underlying, expiry, strike, option type, token, lot size, and tick size. It picks the nearest valid expiry from available data, derives strike spacing from available strikes, and evaluates only one-step ITM, ATM, and one-step OTM candidates.

Quote hard gates reject missing, stale, crossed, illiquid, wide-spread, zero-volume, zero-OI, or premium-out-of-range candidates. If every candidate fails, the console stores `NO_SUITABLE_CONTRACT` and preserves the underlying READY signal.

The quality score includes moneyness, strike proximity, spread percentage, volume, open interest, quote freshness, premium suitability, confidence compatibility, VIX compatibility, and days to expiry. The Option Selection page is recommendation-only: no order is placed, and exits remain controlled by underlying levels.

## Indicator Formulas

- Session VWAP: sum typical price times volume divided by total volume.
- Typical price: `(high + low + close) / 3`.
- EMA 9/21/50: standard multiplier `2 / (period + 1)`.
- ATR 14: average true range over the latest 14 true ranges.
- Opening range: high/low from 09:15 through before 09:30 IST.
- Relative volume: current completed 5-minute candle volume divided by average prior lookback volume.

## Run App

```powershell
pip install -r requirements.txt
streamlit run app.py
```

On macOS/Linux:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Sample Scenarios

- NIFTY 50 opening range breakout: READY
- NIFTY 50 VWAP reclaim: PREPARE
- BANK NIFTY VWAP rejection: PREPARE
- NIFTY 50 breakout with weak relative volume: WAIT
- BANK NIFTY high-VIX unstable context: AVOID

## Workflow Rules

- READY signals can be added to Active Trades.
- PREPARE signals can be added to Watchlist only.
- WAIT, AVOID, and NO_TRADE disable trade actions.
- Duplicate active trades, watchlist entries, and alert-history records are blocked by signal key.
- Alert history recovers safely from missing, empty, or corrupted JSON. Corrupted files are backed up before a fresh empty history is started.

## Verify

```powershell
python -m unittest discover -s tests
```

## GitHub Setup

Use a separate repository, for example `indexpulse-fno-console`.

```bash
git init
git branch -M main
git add .
git commit -m "Prepare Momentum Edge F&O Console cloud preview"
git remote add origin https://github.com/<your-user-or-org>/indexpulse-fno-console.git
git push -u origin main
```

Do not push over the existing Momentum Edge equity repository.

## Streamlit Community Cloud

1. Push this independent repository to GitHub.
2. Open Streamlit Community Cloud.
3. Create a new app.
4. Select repository: `indexpulse-fno-console`.
5. Select branch: `main`.
6. Select app file: `app.py`.
7. Select Python `3.11` if prompted.
8. Add Kite secrets only when ready to test LIVE mode.
9. Reboot the app after changing secrets.
10. Use Streamlit logs to verify dependency installation and startup diagnostics.

Expected initial cloud behavior:

- App starts in SAMPLE mode.
- LIVE mode does not run without complete Kite credentials.
- No Telegram messages are sent.
- No orders are placed.

## Cloud Preview Limitations

- SAMPLE mode is the initial hosted validation mode.
- Streamlit Cloud local files are not durable storage.
- Kite daily authentication flow is not yet complete.
- No Telegram sending.
- No broker order placement.
- Option recommendations remain read-only.
