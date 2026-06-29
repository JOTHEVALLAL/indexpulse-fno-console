# Signal Rule Specification

## Instruments

- NIFTY 50
- BANK NIFTY

Signals are generated from the underlying index. Option contracts are selected only after the underlying signal becomes actionable.

## Timeframes

- Primary signal timeframe: 5-minute
- Trend confirmation timeframe: 15-minute
- Intraday context: daily previous-day levels
- Opening range: 09:15 to 09:30 IST

## Trading Window

- Observation period: 09:15 to 09:25 IST
- First eligible alert: 09:25 IST
- Normal signal window: 09:25 to 14:45 IST
- No new entry after: 14:45 IST
- All intraday positions must be reviewed before market close

## Core Indicators

- Session VWAP
- EMA 9
- EMA 21
- EMA 50
- Opening Range High
- Opening Range Low
- Previous-Day High
- Previous-Day Low
- Average True Range
- Relative volume
- India VIX

## Setup Types

### VWAP Reclaim

Bullish setup where price moves below or near VWAP and subsequently closes back above VWAP with confirmation.

### VWAP Rejection

Bearish setup where price tests VWAP from below or moves briefly above it and subsequently closes below VWAP with confirmation.

### Opening Range Breakout

Bullish setup where price closes above the confirmed opening-range high with adequate momentum and volume.

### Opening Range Breakdown

Bearish setup where price closes below the confirmed opening-range low with adequate momentum and volume.

### EMA Pullback Continuation

Trend-continuation setup where price pulls back towards EMA 9, EMA 21 or VWAP and resumes in the prevailing trend direction.

### Previous-Day Level Breakout

Setup where price closes decisively above the previous-day high or below the previous-day low with supporting volume and acceptable risk.

## Mandatory Signal Output

Signal status values:

- READY: setup is confirmed and all mandatory safety gates pass.
- PREPARE: setup is developing close to its trigger, while risk, trend, volume, and global safety gates remain acceptable.
- WAIT: directional context exists but mandatory confirmation is incomplete.
- AVOID: movement or volatility context is unstable.
- NO_TRADE: instrument, time, or risk context disallows a fresh trade.

Every valid signal contains:

- Instrument
- Signal direction
- Setup name
- Spot price
- VWAP value
- Entry trigger
- Stop loss
- Target 1
- Target 2
- Risk-reward ratio
- Confidence level
- Signal status
- Invalidation condition
- Suggested option side
- Alert timestamp

## Safety Principles

- No signal based solely on option premium movement.
- No automatic order placement.
- No entry without a predefined stop loss.
- No READY status when risk-reward is below the configured minimum.
- No fresh alert when price has already moved excessively beyond entry.
- No duplicate alert for the same instrument, direction, and setup in one session.
- Conflicting indicators result in WAIT or NO_TRADE.
- Highly extended or unstable movement results in AVOID.
- Capital preservation takes priority over alert frequency.
