from config import LOCAL_RSI_TIMEFRAME_SECONDS



def safe_float(
    value,
    default=0
):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def candle_bucket(
    timestamp,
    timeframe_seconds=LOCAL_RSI_TIMEFRAME_SECONDS
):

    timestamp = safe_float(
        timestamp,
        0
    )
    timeframe_seconds = max(
        int(timeframe_seconds or 60),
        1
    )

    return int(timestamp // timeframe_seconds) * timeframe_seconds


def update_candles_from_observation(
    candles,
    *,
    timestamp,
    price,
    volume_5m=0,
    liquidity=0,
    timeframe_seconds=LOCAL_RSI_TIMEFRAME_SECONDS,
    limit=120
):

    price = safe_float(
        price,
        0
    )

    if price <= 0:
        return list(candles or [])

    bucket_start = candle_bucket(
        timestamp,
        timeframe_seconds
    )
    updated = list(candles or [])

    if (
        updated
        and safe_float(updated[-1].get("bucket_start"), 0)
        == bucket_start
    ):
        candle = dict(updated[-1])
        candle["high"] = max(
            safe_float(candle.get("high"), price),
            price
        )
        low = safe_float(
            candle.get("low"),
            price
        )
        candle["low"] = min(
            low if low > 0 else price,
            price
        )
        candle["close"] = price
        candle["volume_5m"] = safe_float(
            volume_5m,
            candle.get("volume_5m", 0)
        )
        candle["liquidity"] = safe_float(
            liquidity,
            candle.get("liquidity", 0)
        )
        candle["observations"] = (
            int(safe_float(candle.get("observations"), 0))
            + 1
        )
        candle["last_observed_at"] = safe_float(
            timestamp,
            bucket_start
        )
        updated[-1] = candle
    else:
        updated.append({
            "bucket_start": bucket_start,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "observations": 1,
            "first_observed_at": safe_float(
                timestamp,
                bucket_start
            ),
            "last_observed_at": safe_float(
                timestamp,
                bucket_start
            ),
            "volume_5m": safe_float(volume_5m, 0),
            "liquidity": safe_float(liquidity, 0)
        })

    updated.sort(
        key=lambda item: safe_float(
            item.get("bucket_start"),
            0
        )
    )

    return updated[-limit:]


def ohlc4(candle):

    close = safe_float(candle.get("close"), 0)
    return (
        safe_float(candle.get("open"), close)
        + safe_float(candle.get("high"), close)
        + safe_float(candle.get("low"), close)
        + close
    ) / 4


def candle_timestamp(candle):

    return safe_float(
        candle.get("bucket_start"),
        safe_float(
            candle.get("timestamp"),
            0
        )
    )


def candle_volume(candle):

    return safe_float(
        candle.get("volume"),
        safe_float(
            candle.get("volume_5m"),
            0
        )
    )


def cumulative_vwap(candles):

    numerator = 0
    denominator = 0

    for candle in candles:
        price = ohlc4(candle)
        volume = candle_volume(candle)

        if price <= 0 or volume <= 0:
            continue

        numerator += price * volume
        denominator += volume

    if denominator <= 0:
        return 0

    return numerator / denominator


def anchored_vwap_from_low(
    candles,
    *,
    lookback_seconds=3600,
    until=None,
    min_candles=3
):

    ready_candles = [
        candle
        for candle in sorted(
            list(candles or []),
            key=candle_timestamp
        )
        if safe_float(candle.get("close"), 0) > 0
        and candle_timestamp(candle) > 0
    ]

    if not ready_candles:
        return {
            "anchored_vwap_enabled": True,
            "anchored_vwap_ready": False,
            "anchored_vwap_reason": "no_candles"
        }

    until_ts = safe_float(
        until,
        candle_timestamp(ready_candles[-1])
    )
    lookback_seconds = max(
        int(lookback_seconds or 3600),
        1
    )
    window_start = until_ts - lookback_seconds

    low_window = [
        candle
        for candle in ready_candles
        if window_start <= candle_timestamp(candle) <= until_ts
    ]

    if len(low_window) < max(int(min_candles or 1), 1):
        return {
            "anchored_vwap_enabled": True,
            "anchored_vwap_ready": False,
            "anchored_vwap_reason": "not_enough_candles",
            "anchored_vwap_candle_count": len(low_window)
        }

    anchor_candle = min(
        low_window,
        key=lambda candle: (
            safe_float(
                candle.get("low"),
                safe_float(candle.get("close"), 0)
            ),
            candle_timestamp(candle)
        )
    )
    anchor_time = candle_timestamp(anchor_candle)
    anchored_candles = [
        candle
        for candle in ready_candles
        if anchor_time <= candle_timestamp(candle) <= until_ts
    ]
    current_vwap = cumulative_vwap(anchored_candles)
    previous_vwap = cumulative_vwap(anchored_candles[:-1])

    if current_vwap <= 0:
        return {
            "anchored_vwap_enabled": True,
            "anchored_vwap_ready": False,
            "anchored_vwap_reason": "no_volume",
            "anchored_vwap_candle_count": len(anchored_candles),
            "anchored_vwap_anchor_timestamp": anchor_time,
            "anchored_vwap_anchor_low": safe_float(
                anchor_candle.get("low"),
                0
            )
        }

    current_price = safe_float(
        anchored_candles[-1].get("close"),
        0
    )
    previous_price = (
        safe_float(anchored_candles[-2].get("close"), current_price)
        if len(anchored_candles) >= 2
        else current_price
    )
    price_above_vwap = current_price >= current_vwap

    return {
        "anchored_vwap_enabled": True,
        "anchored_vwap_ready": True,
        "anchored_vwap_reason": "ready",
        "anchored_vwap": current_vwap,
        "anchored_previous_vwap": previous_vwap,
        "anchored_vwap_anchor": "1h_low",
        "anchored_vwap_anchor_timestamp": anchor_time,
        "anchored_vwap_anchor_low": safe_float(
            anchor_candle.get("low"),
            0
        ),
        "anchored_vwap_candle_count": len(anchored_candles),
        "anchored_vwap_price": current_price,
        "anchored_price_above_vwap": price_above_vwap,
        "anchored_vwap_reclaimed": (
            price_above_vwap
            and previous_vwap > 0
            and previous_price < previous_vwap
        ),
        "anchored_vwap_distance_pct": (
            current_price / max(current_vwap, 1e-18)
            - 1
        )
    }


def anchored_vwap_from_time(
    candles,
    *,
    anchor_timestamp,
    until=None,
    min_candles=3,
    anchor_name="entry"
):

    ready_candles = [
        candle
        for candle in sorted(
            list(candles or []),
            key=candle_timestamp
        )
        if safe_float(candle.get("close"), 0) > 0
        and candle_timestamp(candle) > 0
    ]

    anchor_timestamp = safe_float(
        anchor_timestamp,
        0
    )

    if not ready_candles or anchor_timestamp <= 0:
        return {
            "anchored_vwap_enabled": True,
            "anchored_vwap_ready": False,
            "anchored_vwap_reason": "no_anchor_candles"
        }

    until_ts = safe_float(
        until,
        candle_timestamp(ready_candles[-1])
    )
    anchored_candles = [
        candle
        for candle in ready_candles
        if anchor_timestamp <= candle_timestamp(candle) <= until_ts
    ]

    if len(anchored_candles) < max(int(min_candles or 1), 1):
        return {
            "anchored_vwap_enabled": True,
            "anchored_vwap_ready": False,
            "anchored_vwap_reason": "not_enough_entry_candles",
            "anchored_vwap_candle_count": len(anchored_candles)
        }

    current_vwap = cumulative_vwap(anchored_candles)
    previous_vwap = cumulative_vwap(anchored_candles[:-1])

    if current_vwap <= 0:
        return {
            "anchored_vwap_enabled": True,
            "anchored_vwap_ready": False,
            "anchored_vwap_reason": "no_volume",
            "anchored_vwap_candle_count": len(anchored_candles),
            "anchored_vwap_anchor_timestamp": anchor_timestamp
        }

    current_price = safe_float(
        anchored_candles[-1].get("close"),
        0
    )
    previous_price = (
        safe_float(anchored_candles[-2].get("close"), current_price)
        if len(anchored_candles) >= 2
        else current_price
    )
    price_above_vwap = current_price >= current_vwap

    return {
        "anchored_vwap_enabled": True,
        "anchored_vwap_ready": True,
        "anchored_vwap_reason": "ready",
        "anchored_vwap": current_vwap,
        "anchored_previous_vwap": previous_vwap,
        "anchored_vwap_anchor": anchor_name,
        "anchored_vwap_anchor_timestamp": anchor_timestamp,
        "anchored_vwap_anchor_low": min(
            safe_float(
                candle.get("low"),
                safe_float(candle.get("close"), 0)
            )
            for candle in anchored_candles
        ),
        "anchored_vwap_candle_count": len(anchored_candles),
        "anchored_vwap_price": current_price,
        "anchored_price_above_vwap": price_above_vwap,
        "anchored_vwap_reclaimed": (
            price_above_vwap
            and previous_vwap > 0
            and previous_price < previous_vwap
        ),
        "anchored_vwap_distance_pct": (
            current_price / max(current_vwap, 1e-18)
            - 1
        )
    }
