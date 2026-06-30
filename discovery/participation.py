"""Participation / breadth provider — the decisive lattice axis.

Combines two INDEPENDENT signals into a breadth score in [-1, 1] (the contract
of ParticipationProvider in lattice.py):

  concentration  (Alchemy Solana RPC, no extra key):
      top-N holder-account share of supply via getTokenLargestAccounts +
      getTokenSupply. High concentration -> NEGATIVE (manufactured / rug risk).

  unique buyers  (Helius, requires HELIUS_API_KEY):
      distinct buyer wallets over a recent window via parsed swaps.
      Many distinct buyers -> POSITIVE (organic breadth).

Graceful degradation: with a Helius key, breadth blends both; without one, it
falls back to concentration-only (still non-null, still better than blind); on
a non-Solana mint or any error, returns None (blind).

Cheap by construction: only called for pipeline candidates (post universe+
lattice+conviction), with a per-token TTL cache. Network calls are sync
urllib with a short timeout — fine at candidate volume.
"""
import json
import os
import time
import urllib.request

import config
from discovery.lattice import ParticipationProvider

SOLANA_RPC = (getattr(config, "ALCHEMY_RPC_URLS", {}) or {}).get("solana") or ""
HELIUS_KEY = (getattr(config, "HELIUS_API_KEY", "") or os.environ.get("HELIUS_API_KEY", "")).strip()

# Helius's enhanced API sits behind Cloudflare, which 403s urllib's default
# User-Agent (error 1010). A normal UA header is required.
_HEADERS = {"Content-Type": "application/json", "User-Agent": "lattice-scanner/1.0"}

# Accounts that are not "holders" for concentration purposes (burn, native mint).
_BURN = {"1nc1nerator11111111111111111111111111111111",
         "11111111111111111111111111111111"}


def _clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _rpc(url, method, params, timeout=6):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("result")


def concentration_share(mint, top_n=10, exclude_largest=True):
    """Top-N holder-account share of supply in [0,1] (LP/pool roughly excluded
    by dropping the single largest account). None on error/no-RPC."""
    if not SOLANA_RPC:
        return None
    try:
        largest = _rpc(SOLANA_RPC, "getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        supply = _rpc(SOLANA_RPC, "getTokenSupply", [mint, {"commitment": "confirmed"}])
        accts = (largest or {}).get("value") or []
        total = float(((supply or {}).get("value") or {}).get("uiAmount") or 0)
        if total <= 0 or not accts:
            return None
        amts = []
        for a in accts:
            if a.get("address") in _BURN:
                continue
            ui = a.get("uiAmount")
            if ui is None:
                ui = float(a.get("amount", 0)) / (10 ** int(a.get("decimals", 0) or 0))
            amts.append(float(ui or 0))
        amts.sort(reverse=True)
        if exclude_largest and len(amts) > 1:
            amts = amts[1:]          # drop the presumed LP/pool vault
        return _clamp(sum(amts[:top_n]) / total, 0.0, 1.0)
    except Exception:
        return None


def unique_buyers_signal(mint, window_seconds=3600, max_sigs=100):
    """Breadth from distinct buyer wallets via Helius parsed swaps. Returns a
    signal in [-1,1] (more distinct buyers -> higher), or None if no key/data.

    NOTE: the Helius response shape is validated against live responses once
    HELIUS_API_KEY is set; parsing is best-effort until then."""
    if not HELIUS_KEY:
        return None
    try:
        # recent signatures touching the mint, then parse them via Helius.
        rpc = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
        sigs = _rpc(rpc, "getSignaturesForAddress", [mint, {"limit": max_sigs}]) or []
        cutoff = time.time() - window_seconds
        sig_list = [s["signature"] for s in sigs
                    if (s.get("blockTime") or 0) >= cutoff][:max_sigs]
        if not sig_list:
            return None
        url = f"https://api.helius.xyz/v0/transactions?api-key={HELIUS_KEY}"
        body = json.dumps({"transactions": sig_list}).encode()
        req = urllib.request.Request(url, data=body, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as r:
            parsed = json.loads(r.read().decode())
        buyers, sellers = set(), set()
        for tx in parsed or []:
            if tx.get("type") not in ("SWAP", None):
                continue
            for tt in tx.get("tokenTransfers", []) or []:
                if tt.get("mint") != mint:
                    continue
                if tt.get("toUserAccount"):
                    buyers.add(tt["toUserAccount"])      # received this token = buy
                if tt.get("fromUserAccount"):
                    sellers.add(tt["fromUserAccount"])   # sent this token = sell
        n_buy = len(buyers)
        if n_buy == 0 and not sellers:
            return None
        # breadth: many distinct buyers AND buyers outnumbering sellers -> positive.
        breadth = min(n_buy / 25.0, 1.0)                 # ~25 distinct buyers/window saturates
        asym = (n_buy - len(sellers)) / max(n_buy + len(sellers), 1)
        return _clamp(0.6 * (2 * breadth - 1) + 0.4 * asym)
    except Exception:
        return None


class HeliusAlchemyParticipationProvider(ParticipationProvider):
    def __init__(self, ttl_seconds=180, conc_weight=0.5, buyers_weight=0.5):
        self.ttl = ttl_seconds
        self.cw, self.bw = conc_weight, buyers_weight
        self._cache = {}   # mint -> (ts, detail)

    def _compute(self, token_address, window_seconds):
        conc = concentration_share(token_address)                     # [0,1] or None
        buyers = unique_buyers_signal(token_address, window_seconds)  # [-1,1] or None
        # The unique-buyers signal is the decisive one. Without it we return
        # None (blind) rather than acting on concentration alone, which is
        # misleading for bonding-curve tokens.
        if buyers is None:
            val = None
        elif conc is not None:
            conc_sig = _clamp(1.0 - 2.0 * conc)            # 0%->+1, 50%->0, 100%->-1
            val = _clamp(self.cw * conc_sig + self.bw * buyers)
        else:
            val = buyers
        return {"breadth": val, "concentration": conc, "buyers_sig": buyers}

    def breadth_detail(self, token_address, window_seconds=3600):
        now = time.time()
        hit = self._cache.get(token_address)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        d = self._compute(token_address, window_seconds)
        self._cache[token_address] = (now, d)
        return d

    def breadth(self, token_address, window_seconds=3600):
        return self.breadth_detail(token_address, window_seconds)["breadth"]


def status():
    return {"solana_rpc": bool(SOLANA_RPC), "helius_key": bool(HELIUS_KEY)}
