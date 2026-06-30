"""Multi-axis lattice scoring + anti-fakeness vetoes.

"Lattice" = corroboration across INDEPENDENT axes, not one metric. A move is
real when flow, liquidity behaviour, price structure, and — the decisive axis —
PARTICIPATION BREADTH agree. Flow/price alone is the most gameable signal; our
own outcome data confirms a model on just those axes barely beats random
(OOS AUC ~0.57). So participation is a first-class input here.

It is also the one axis the current schema lacks. Rather than fake it, we define
a provider interface and degrade gracefully: with no provider, the breadth axis
is dropped and the verdict is flagged `participation_blind=True` so callers know
the lattice read is partial.
"""

from abc import ABC, abstractmethod


class ParticipationProvider(ABC):
    """Returns a breadth signal in [-1, 1] for a token over a recent window:
      +1  many distinct buyers / holder count rising / low wallet concentration
       0  neutral / unknown
      -1  a few wallets dominate buys (wash / manufactured move)

    A real implementation queries an on-chain indexer (Helius/Birdeye/etc.) for
    unique buyers, new-holder rate, and top-N wallet share, then maps to [-1,1].
    THIS IS THE PRIMARY UNLOCK — see README. Until one is wired, use the Null
    provider; the scorer will run participation-blind."""

    @abstractmethod
    def breadth(self, token_address, window_seconds=3600):
        raise NotImplementedError


class NullParticipationProvider(ParticipationProvider):
    def breadth(self, token_address, window_seconds=3600):
        return None


def _clamp01(x):
    return max(0.0, min(1.0, x))


def lattice_verdict(row, *, participation=None, liquidity_change_pct=None):
    """row: signal_snapshots-shaped dict. Returns a structured verdict:
      axes: per-axis [0,1] scores (interpretable evidence)
      vetoes: list of hard rejection reasons (anti-fakeness)
      participation_blind: True when no breadth data was available
      composite: blended [0,1] lattice score over the AVAILABLE axes
    """
    def f(k, d=0.0):
        try:
            v = row.get(k)
            return float(v) if v is not None else d
        except (TypeError, ValueError):
            return d

    pc5 = f("price_change_5m")
    vlr = f("volume_liquidity_ratio")
    bsr = f("buy_sell_ratio")
    b5, s5 = f("buy_volume_5m"), f("sell_volume_5m")
    asym = (b5 - s5) / (b5 + s5) if (b5 + s5) > 0 else 0.0

    axes = {}
    # flow quality: net buy asymmetry + sustained buy/sell ratio
    axes["flow"] = _clamp01(0.5 * (asym + 1) * _clamp01(bsr / 2.0))
    # liquidity health: healthy VLR band (real volume, not a thin-book pump),
    # plus liquidity not draining
    vlr_band = _clamp01(1.0 - abs(min(vlr, 4.0) - 0.5) / 2.0)
    liq_trend = 0.5 if liquidity_change_pct is None else _clamp01(0.5 + liquidity_change_pct)
    axes["liquidity"] = _clamp01(0.5 * vlr_band + 0.5 * liq_trend)
    # structure: pc5 is a PERCENT (data p99~11, max~95). Healthy revival
    # impulse sweet spot ~15%; falls off toward flat and toward blow-off.
    axes["structure"] = _clamp01(1.0 - abs(pc5 - 15.0) / 35.0) if pc5 > 0 else 0.3

    participation_blind = participation is None
    if not participation_blind:
        axes["participation"] = _clamp01(0.5 * (participation + 1))

    vetoes = []
    # exit-pump / rug: price climbing while liquidity bleeds
    if liquidity_change_pct is not None and liquidity_change_pct <= -0.15 and pc5 > 0:
        vetoes.append("liquidity_draining_while_price_up")
    # parabolic blow-off: pc5 is PERCENT; 150% in 5m is past even the most
    # permissive (early-revival) entry cap in the live system.
    if pc5 > 150.0:
        vetoes.append("parabolic_blowoff_5m")
    # thin-book: huge VLR usually means a hollow pump on no real depth
    if vlr > 4.0:
        vetoes.append("thin_book_extreme_vlr")
    # manufactured flow: high volume but breadth says a few wallets (only when known)
    if (not participation_blind) and participation is not None and participation < -0.4 and vlr > 1.0:
        vetoes.append("flow_without_breadth_wash")

    composite = sum(axes.values()) / len(axes) if axes else 0.0
    return {
        "axes": axes,
        "vetoes": vetoes,
        "participation_blind": participation_blind,
        "composite": composite,
        "passed": len(vetoes) == 0,
    }
