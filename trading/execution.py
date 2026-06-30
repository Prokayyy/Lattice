import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode

import aiohttp
import base58

from config import (
    GMGN_API_KEY,
    GMGN_PRIVATE_KEY,
    GMGN_TRADING_ANTI_MEV,
    GMGN_TRADING_CONDITION_ORDERS_ENABLED,
    GMGN_TRADING_CONFIRM_LIVE,
    GMGN_TRADING_ENABLED,
    GMGN_TRADING_PRIORITY_FEE_SOL,
    GMGN_TRADING_SLIPPAGE_PCT,
    GMGN_TRADING_STOP_LOSS_PCT,
    GMGN_TRADING_TAKE_PROFIT_MULTIPLE,
    GMGN_TRADING_TAKE_PROFIT_SELL_RATIO_PCT,
    GMGN_TRADING_TIP_FEE_SOL,
    GMGN_TRADING_TP_TRAILING_DRAWDOWN_PCT,
    GMGN_TRADING_WALLET,
)

from config import (
    ALCHEMY_RPC_URLS,
    DEFINITIVE_ABORT_ON_QUOTE_WARNINGS,
    DEFINITIVE_ALLOWED_CHAINS,
    DEFINITIVE_API_BASE_URL,
    DEFINITIVE_API_KEY,
    DEFINITIVE_API_MODE,
    DEFINITIVE_API_SECRET,
    DEFINITIVE_BASE_CONTRA_ASSET,
    DEFINITIVE_BSC_CONTRA_ASSET,
    DEFINITIVE_DEFAULT_CONTRA_ASSET,
    DEFINITIVE_ENTRY_CONFIRM_FILL_SECONDS,
    DEFINITIVE_ENTRY_MAX_PRICE_IMPACT,
    DEFINITIVE_ENTRY_SECONDS_TO_EXPIRE,
    DEFINITIVE_ETHEREUM_CONTRA_ASSET,
    DEFINITIVE_EXECUTION_CONFIRM_LIVE,
    DEFINITIVE_EXECUTION_ENABLED,
    DEFINITIVE_EXIT_MAX_PRICE_IMPACT,
    DEFINITIVE_HYPEREVM_CONTRA_ASSET,
    DEFINITIVE_MAX_ACCOUNT_EXPOSURE_USD,
    DEFINITIVE_MAX_ENTRY_NOTIONAL_USD,
    DEFINITIVE_MAX_OPEN_POSITIONS,
    DEFINITIVE_MAX_PRICE_IMPACT,
    DEFINITIVE_MIN_ENTRY_NOTIONAL_USD,
    DEFINITIVE_MIRROR_PAPER_POSITION_SIZE,
    DEFINITIVE_PORTFOLIO_ID,
    DEFINITIVE_QUOTE_BEFORE_SUBMIT,
    DEFINITIVE_SECONDS_TO_EXPIRE,
    DEFINITIVE_SELL_CONFIRM_FILL_SECONDS,
    DEFINITIVE_SELL_ORDER_ENDPOINT,
    DEFINITIVE_SLIPPAGE_TOLERANCE,
    DEFINITIVE_SOLANA_CONTRA_ASSET,
    DEFINITIVE_SUBMIT_MAX_ATTEMPTS,
    DEFINITIVE_SUBMIT_RETRY_DELAY_SECONDS,
    DEFINITIVE_USE_DISPLAY_ASSET_PRICE,
    LIVE_EXECUTION_DRY_RUN,
    LIVE_EXECUTION_ENABLED,
    LIVE_EXECUTION_QUOTE_CHECK_ENABLED,
    LIVE_EXECUTION_QUOTE_REFRESH_SECONDS,
    DEFINITIVE_FLASH_API_KEY,
    DEFINITIVE_FLASH_API_BASE_URL,
    DEFINITIVE_FLASH_CONFIRM_LIVE,
    DEFINITIVE_FLASH_ENABLED,
    DEFINITIVE_FLASH_FUNDER_ADDRESS,
    DEFINITIVE_FLASH_PRIVATE_KEY,
    DEFINITIVE_FLASH_MAX_SLIPPAGE,
    DEFINITIVE_FLASH_MAX_PRICE_IMPACT,
    DEFINITIVE_FLASH_CONFIRM_FILL_SECONDS,
    DEFINITIVE_FLASH_SUBMIT_MAX_ATTEMPTS,
    DEFINITIVE_FLASH_SUBMIT_RETRY_DELAY_SECONDS,
    DEFINITIVE_FLASH_WRAP_SETTLE_SECONDS,
    DEFINITIVE_FLASH_ONCHAIN_STOP_ENABLED,
    DEFINITIVE_FLASH_ONCHAIN_STOP_ORDER_TYPE,
    DEFINITIVE_FLASH_ONCHAIN_STOP_RATCHET_MIN_PCT,
    DEFINITIVE_FLASH_RESTING_EXITS_ENABLED,
    HELIUS_API_KEY
)


SOL_MINT = "So11111111111111111111111111111111111111112"

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def solana_rpc_urls():
    urls = []
    if HELIUS_API_KEY:
        urls.append(
            "https://mainnet.helius-rpc.com/?api-key=" + HELIUS_API_KEY
        )

    primary = ALCHEMY_RPC_URLS.get("solana", "")
    if primary and primary not in urls:
        urls.append(primary)

    return urls


def safe_float(
    value,
    default=0
):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def safe_int(
    value,
    default=0
):

    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def raw_amount_from_tokens(
    amount_tokens,
    decimals
):

    amount = Decimal(str(amount_tokens or 0))
    scale = Decimal(10) ** int(decimals or 0)
    raw = (
        amount
        * scale
    ).to_integral_value(
        rounding=ROUND_DOWN
    )

    return str(max(int(raw), 0))


def tokens_from_raw_amount(
    raw_amount,
    decimals
):

    raw = Decimal(str(raw_amount or 0))
    scale = Decimal(10) ** int(decimals or 0)

    if scale <= 0:
        return 0

    return float(raw / scale)


def bps_to_fraction_string(
    bps
):

    fraction = max(
        safe_float(bps, 0) / 10000,
        0
    )
    return (
        f"{fraction:.6f}"
        .rstrip("0")
        .rstrip(".")
        or "0"
    )


def bps_to_percent_string(
    bps
):

    percent = max(
        safe_float(bps, 0) / 100,
        0
    )
    return (
        f"{percent:.6f}"
        .rstrip("0")
        .rstrip(".")
        or "0"
    )


def percent_string(
    value
):

    percent = max(
        safe_float(value, 0),
        0
    )
    return (
        f"{percent:.6f}"
        .rstrip("0")
        .rstrip(".")
        or "0"
    )


def decimal_string(
    value,
    places=12
):

    amount = Decimal(str(value or 0))

    if amount <= 0:
        return "0"

    quant = Decimal(10) ** -int(places or 0)
    text = format(
        amount.quantize(
            quant,
            rounding=ROUND_DOWN
        ),
        "f"
    )
    return (
        text.rstrip("0").rstrip(".")
        or "0"
    )


def compact_json(
    value
):

    if value is None:
        return ""

    return json.dumps(
        value,
        separators=(",", ":"),
        ensure_ascii=False
    )


def quote_output_value_usd(
    output_mint,
    output_amount,
    output_price_usd=0,
    token_unit_price=0
):

    output_amount = safe_float(output_amount, 0)
    token_unit_price = safe_float(token_unit_price, 0)

    if token_unit_price > 0:
        return output_amount * token_unit_price

    if output_mint in (USDC_MINT,):
        return output_amount

    if output_mint == SOL_MINT:
        return output_amount * safe_float(
            output_price_usd,
            0
        )

    return 0


class SolanaTokenDecimalsCache:

    def __init__(
        self,
        ttl_seconds=3600
    ):

        self.ttl_seconds = ttl_seconds
        self.cache = {}

    async def get_decimals(
        self,
        mint
    ):

        mint = str(mint or "")

        if mint == SOL_MINT:
            return 9

        if mint == USDC_MINT:
            return 6

        cached = self.cache.get(mint)
        now = time.time()

        if cached and now - cached["timestamp"] < self.ttl_seconds:
            return cached["decimals"]

        urls = solana_rpc_urls()

        if not urls:
            raise RuntimeError("missing_solana_rpc_url")
        rpc_url = urls[0]

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenSupply",
            "params": [mint]
        }

        timeout = aiohttp.ClientTimeout(total=10)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(rpc_url, json=payload) as response:
                data = await response.json(content_type=None)

        if data.get("error"):
            raise RuntimeError(
                data["error"].get("message", "token_decimals_error")
            )

        decimals = safe_int(
            (
                data.get("result", {})
                .get("value", {})
                .get("decimals")
            ),
            0
        )
        self.cache[mint] = {
            "decimals": decimals,
            "timestamp": now
        }

        return decimals



class DefinitiveQuickTradeClient:

    def __init__(
        self
    ):

        self.base_url = DEFINITIVE_API_BASE_URL.rstrip("/")

    def configured(
        self
    ):

        return bool(
            DEFINITIVE_API_KEY
            and DEFINITIVE_API_SECRET
            and self.base_url
        )

    def path(
        self,
        suffix
    ):

        suffix = "/" + str(suffix or "").lstrip("/")

        if DEFINITIVE_API_MODE == "organization":
            if not DEFINITIVE_PORTFOLIO_ID:
                return ""

            return (
                "/v2/organization/portfolios/"
                f"{DEFINITIVE_PORTFOLIO_ID}"
                f"{suffix}"
            )

        return f"/v2/portfolio{suffix}"

    def auth_headers(
        self,
        method,
        path,
        query_params=None,
        body=None
    ):

        timestamp = str(
            int(time.time() * 1000)
        )
        headers = {
            "x-definitive-api-key": DEFINITIVE_API_KEY,
            "x-definitive-timestamp": timestamp
        }
        filtered = [
            (key, value)
            for key, value in headers.items()
            if key.lower().startswith("x-definitive-")
        ]
        sorted_headers = ",".join(
            f"{key}:{compact_json(value)}"
            for key, value in sorted(
                filtered,
                key=lambda item: item[0]
            )
        )
        query_string = urlencode(
            query_params or {}
        )
        body_string = compact_json(body)
        message = (
            f"{method.upper()}:{path}?{query_string}:"
            f"{timestamp}:{sorted_headers}{body_string}"
        )
        secret = DEFINITIVE_API_SECRET

        if secret.startswith("dpks_"):
            secret = secret[5:]

        signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        return dict(
            headers,
            **{
                "x-definitive-signature": signature,
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
        )

    async def request(
        self,
        method,
        suffix,
        *,
        body=None,
        query_params=None
    ):

        if not self.configured():
            return {
                "ok": False,
                "provider": "definitive",
                "error": "definitive_credentials_missing"
            }

        path = self.path(suffix)

        if not path:
            return {
                "ok": False,
                "provider": "definitive",
                "error": "definitive_portfolio_id_missing"
            }

        query_string = urlencode(
            query_params or {}
        )
        url = (
            f"{self.base_url}{path}?{query_string}"
            if query_string
            else f"{self.base_url}{path}"
        )
        timeout = aiohttp.ClientTimeout(total=15)
        request_body = compact_json(body) if body is not None else None

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method.upper(),
                url,
                data=request_body,
                headers=self.auth_headers(
                    method,
                    path,
                    query_params=query_params,
                    body=body
                )
            ) as response:
                try:
                    data = await response.json(content_type=None)
                except Exception:
                    data = {
                        "text": await response.text()
                    }

                if response.status >= 400:
                    return {
                        "ok": False,
                        "provider": "definitive",
                        "status": response.status,
                        "error": (
                            data.get("error")
                            or data.get("message")
                            or data.get("text")
                            or str(data)
                        ),
                        "raw_response": data
                    }

        return {
            "ok": True,
            "provider": "definitive",
            "status": response.status,
            "raw_response": data
        }

    async def quicktrade_quote(
        self,
        body
    ):

        return await self.request(
            "POST",
            "/quicktrade/quote",
            body=body
        )

    async def quicktrade_submit(
        self,
        body
    ):

        return await self.request(
            "POST",
            "/quicktrade",
            body=body
        )

    async def trade_quote(
        self,
        body
    ):

        return await self.request(
            "POST",
            "/trade/quote",
            body=body
        )

    async def trade_submit(
        self,
        body
    ):

        return await self.request(
            "POST",
            "/trade",
            body=body
        )


class DefinitiveFlashClient:

    FLASH_PATH = "/v1"

    def __init__(
        self
    ):

        self.base_url = DEFINITIVE_FLASH_API_BASE_URL.rstrip("/")

    def configured(
        self
    ):

        return bool(
            DEFINITIVE_FLASH_API_KEY
            and DEFINITIVE_FLASH_FUNDER_ADDRESS
            and DEFINITIVE_FLASH_PRIVATE_KEY
        )

    def headers(
        self
    ):

        return {
            "x-definitive-api-key": DEFINITIVE_FLASH_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _ed25519_private_key(
        self
    ):

        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey
        )
        key_bytes = base58.b58decode(DEFINITIVE_FLASH_PRIVATE_KEY)
        # Solana secret keys are 64 bytes (seed + pubkey); the first 32 are the
        # Ed25519 seed. A bare 32-byte seed is accepted unchanged.
        return Ed25519PrivateKey.from_private_bytes(key_bytes[:32])

    def sign_svm_order_message(
        self,
        order_message
    ):

        # Flash SVM: svm.orderMessage is a UTF-8 string signed directly with
        # Ed25519 and returned base58-encoded as userSignature (verified vs
        # OpenAPI 2026-05-30). It is NOT base64 — do not decode it.
        private_key = self._ed25519_private_key()
        message_bytes = order_message.encode("utf-8")
        return base58.b58encode(private_key.sign(message_bytes)).decode()

    def cancel_message(
        self,
        order_id
    ):

        # Exact plaintext the Flash program rebuilds and verifies; same bytes
        # across EVM and SVM. The dash is an em dash (U+2014).
        return (
            "Definitive Flash v1 — Cancel Order\nOrder: "
            + str(order_id)
        )

    def sign_cancel_message(
        self,
        order_id
    ):

        private_key = self._ed25519_private_key()
        message_bytes = self.cancel_message(order_id).encode("utf-8")
        return base58.b58encode(private_key.sign(message_bytes)).decode()

    def sign_svm_sponsored_delegate_tx(
        self,
        tx_b64
    ):

        # First trade for a token returns a sponsor-paid (gasless) delegate
        # VersionedTransaction as base64. We partial-sign it with the funder
        # wallet: fill ONLY our signature slot and leave the sponsor (fee
        # payer) slot untouched so Definitive can co-sign and broadcast.
        from solders.keypair import Keypair
        from solders.transaction import VersionedTransaction

        key_bytes = base58.b58decode(DEFINITIVE_FLASH_PRIVATE_KEY)
        if len(key_bytes) >= 64:
            keypair = Keypair.from_bytes(key_bytes[:64])
        else:
            keypair = Keypair.from_seed(key_bytes[:32])

        tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        message = tx.message
        signatures = list(tx.signatures)
        account_keys = list(message.account_keys)
        index = account_keys.index(keypair.pubkey())
        signatures[index] = keypair.sign_message(bytes(message))
        signed = VersionedTransaction.populate(message, signatures)
        return base64.b64encode(bytes(signed)).decode()

    async def request(
        self,
        method,
        path_suffix,
        *,
        body=None,
        query_params=None
    ):

        url = (
            self.base_url
            + self.FLASH_PATH
            + "/"
            + str(path_suffix or "").lstrip("/")
        )

        if query_params:
            url += "?" + urlencode(query_params)

        timeout = aiohttp.ClientTimeout(total=15)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method.upper(),
                url,
                data=compact_json(body) if body is not None else None,
                headers=self.headers()
            ) as response:
                try:
                    data = await response.json(content_type=None)
                except Exception:
                    data = {"text": await response.text()}

                if response.status >= 400:
                    return {
                        "ok": False,
                        "provider": "flash",
                        "status": response.status,
                        "error": (
                            data.get("error")
                            or data.get("message")
                            or data.get("text")
                            or str(data)
                        ),
                        "raw_response": data
                    }

        return {
            "ok": True,
            "provider": "flash",
            "status": response.status,
            "raw_response": data
        }

    async def quote(
        self,
        body
    ):

        return await self.request("POST", "quote", body=body)

    async def submit_order(
        self,
        body
    ):

        return await self.request("POST", "order", body=body)

    async def get_order(
        self,
        order_id
    ):

        return await self.request(
            "GET",
            f"orders/{order_id}",
            query_params={
                "funderAddress": DEFINITIVE_FLASH_FUNDER_ADDRESS
            }
        )

    async def list_orders(
        self,
        *,
        status=None,
        page_size=None
    ):

        query_params = {
            "funderAddress": DEFINITIVE_FLASH_FUNDER_ADDRESS
        }
        if status:
            query_params["statuses"] = status
        if page_size:
            query_params["pageSize"] = page_size

        return await self.request(
            "GET",
            "orders",
            query_params=query_params
        )

    async def cancel_order(
        self,
        order_id
    ):

        # CancelOrderRequest is exactly {cancelMessage, userSignature}; the
        # orderId lives in the path and the signature proves ownership.
        body = {
            "cancelMessage": self.cancel_message(order_id),
            "userSignature": self.sign_cancel_message(order_id)
        }
        return await self.request(
            "POST",
            f"orders/{order_id}/cancel",
            body=body
        )

    # ── Solana on-chain onboarding (non-sponsored delegate path) ─────────────
    # When a quote returns svm.delegateIx and NO svm.sponsoredDelegateTx,
    # Definitive is not sponsoring the gasless setup, so WE must put the token
    # accounts in place and grant the Flash program delegate authority on-chain
    # before submitting the order:
    #   buy  → create the wSOL (contra) ATA, wrap the trade's SOL, syncNative,
    #          and Approve the delegate over the wSOL ATA;
    #   sell → ensure the contra ATA exists (to receive proceeds) and Approve
    #          the delegate over the target-token ATA.
    # This is the manual equivalent of the sponsoredDelegateTx Definitive
    # bundles when sponsorship IS offered. Granting delegate is one-time per
    # token account; later quotes return delegateIx=null. Verified live
    # 2026-06-04 (mainnet, BONK-style Token-2022 mint).

    TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
    ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
    SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
    # Extra lamports wrapped on top of the trade spend so a small price move
    # between onboarding and submit cannot leave the wSOL balance short.
    SVM_WRAP_BUFFER_LAMPORTS = 2_000_000

    def solana_keypair(
        self
    ):

        from solders.keypair import Keypair

        key_bytes = base58.b58decode(DEFINITIVE_FLASH_PRIVATE_KEY)
        if len(key_bytes) >= 64:
            return Keypair.from_bytes(key_bytes[:64])
        return Keypair.from_seed(key_bytes[:32])

    async def solana_rpc(
        self,
        method,
        params,
        retries=3
    ):

        # Robust JSON-RPC: rotate Alchemy <-> Helius and retry on a non-JSON or
        # connection failure. Intermittent rate-limit / HTML error pages would
        # otherwise raise a JSONDecodeError and break onboarding. Solana txs
        # dedupe by signature, so retrying sendTransaction is safe.
        import json as _json
        urls = solana_rpc_urls()
        if not urls:
            return {"error": {"message": "missing_solana_rpc_url"}}

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        timeout = aiohttp.ClientTimeout(total=20)
        last_err = "rpc_unavailable"

        for attempt in range(max(int(retries), 1)):
            url = urls[attempt % len(urls)]
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, json=payload) as response:
                        status = response.status
                        text = await response.text()
                try:
                    return _json.loads(text)
                except Exception:
                    last_err = f"non_json_status_{status}"
            except Exception as exc:
                last_err = str(exc)
            await asyncio.sleep(0.4)

        return {"error": {"message": last_err}}

    async def latest_solana_blockhash(
        self
    ):

        data = await self.solana_rpc(
            "getLatestBlockhash",
            [{"commitment": "finalized"}]
        )
        return (
            (data.get("result", {}) or {})
            .get("value", {})
            .get("blockhash", "")
        )

    async def confirm_solana_signature(
        self,
        signature,
        timeout_seconds=45
    ):

        deadline = time.time() + max(safe_float(timeout_seconds, 0), 0)
        while time.time() <= deadline:
            await asyncio.sleep(2.5)
            data = await self.solana_rpc(
                "getSignatureStatuses",
                [[signature]]
            )
            value = (
                (data.get("result", {}) or {}).get("value")
                or [None]
            )[0]
            if value:
                if value.get("err"):
                    return {
                        "ok": False,
                        "error": f"onchain_error:{value.get('err')}",
                        "signature": signature
                    }
                if value.get("confirmationStatus") in (
                    "confirmed",
                    "finalized"
                ):
                    return {"ok": True, "signature": signature}
        return {
            "ok": False,
            "error": "confirm_timeout",
            "signature": signature
        }

    async def solana_token_program(
        self,
        mint
    ):

        info = await self.solana_rpc(
            "getAccountInfo",
            [str(mint), {"encoding": "base64"}]
        )
        value = (info.get("result", {}) or {}).get("value") or {}
        return value.get("owner") or self.TOKEN_PROGRAM_ID

    def derive_associated_token_account(
        self,
        owner,
        mint,
        token_program
    ):

        from solders.pubkey import Pubkey

        ata, _ = Pubkey.find_program_address(
            [
                bytes(owner),
                bytes(Pubkey.from_string(str(token_program))),
                bytes(Pubkey.from_string(str(mint)))
            ],
            Pubkey.from_string(self.ASSOCIATED_TOKEN_PROGRAM_ID)
        )
        return ata

    def _create_ata_idempotent_ix(
        self,
        payer,
        owner,
        mint,
        token_program,
        ata
    ):

        from solders.pubkey import Pubkey
        from solders.instruction import Instruction, AccountMeta

        return Instruction(
            Pubkey.from_string(self.ASSOCIATED_TOKEN_PROGRAM_ID),
            bytes([1]),
            [
                AccountMeta(payer, True, True),
                AccountMeta(ata, False, True),
                AccountMeta(owner, False, False),
                AccountMeta(Pubkey.from_string(str(mint)), False, False),
                AccountMeta(
                    Pubkey.from_string(self.SYSTEM_PROGRAM_ID),
                    False,
                    False
                ),
                AccountMeta(
                    Pubkey.from_string(str(token_program)),
                    False,
                    False
                )
            ]
        )

    def _sync_native_ix(
        self,
        wsol_ata
    ):

        from solders.pubkey import Pubkey
        from solders.instruction import Instruction, AccountMeta

        return Instruction(
            Pubkey.from_string(self.TOKEN_PROGRAM_ID),
            bytes([17]),
            [AccountMeta(wsol_ata, False, True)]
        )

    def _delegate_ix_from_quote(
        self,
        delegate_ix
    ):

        from solders.pubkey import Pubkey
        from solders.instruction import Instruction, AccountMeta

        return Instruction(
            Pubkey.from_string(delegate_ix["programId"]),
            base58.b58decode(delegate_ix["data"]),
            [
                AccountMeta(
                    Pubkey.from_string(a["pubkey"]),
                    bool(a.get("isSigner")),
                    bool(a.get("isWritable"))
                )
                for a in delegate_ix.get("accounts", [])
            ]
        )

    async def wsol_balance_lamports(
        self
    ):

        # Current wrapped-SOL balance in the funder's wSOL ATA, in lamports
        # (wSOL has 9 decimals, so base units == lamports). 0 if the ATA does not
        # exist yet.
        try:
            owner = self.solana_keypair().pubkey()
            wsol_ata = self.derive_associated_token_account(
                owner,
                DEFINITIVE_SOLANA_CONTRA_ASSET,
                self.TOKEN_PROGRAM_ID
            )
            data = await self.solana_rpc(
                "getTokenAccountBalance",
                [str(wsol_ata), {"commitment": "confirmed"}]
            )
            amount = (
                (data.get("result", {}) or {}).get("value", {}) or {}
            ).get("amount")
            return int(amount) if amount is not None else 0
        except Exception:
            return 0

    async def ensure_svm_onboarding(
        self,
        svm,
        *,
        target_mint,
        contra_mint,
        wrap_lamports=0,
        simulate_only=False
    ):

        # Build -> simulate -> broadcast -> confirm the one-time setup tx for a
        # non-sponsored SVM order. No-op when the quote carries no delegateIx
        # (already delegated, or sponsorship offered). Returns
        # {ok, signature?, error?, skipped?}.
        delegate_ix = (
            svm.get("delegateIx") if isinstance(svm, dict) else None
        )
        needs_wrap = bool(wrap_lamports and int(wrap_lamports) > 0)
        # Skip only if neither a one-time delegate grant NOR a wSOL wrap is
        # needed. The wrap is required on EVERY buy (Definitive pulls pre-wrapped
        # wSOL via the delegate), even after the wallet is already delegated.
        if not delegate_ix and not needs_wrap:
            return {"ok": True, "skipped": True, "reason": "nothing_to_do"}

        from solders.hash import Hash
        from solders.message import MessageV0
        from solders.transaction import VersionedTransaction
        from solders.system_program import transfer, TransferParams

        try:
            keypair = self.solana_keypair()
            owner = keypair.pubkey()
            wsol_ata = self.derive_associated_token_account(
                owner,
                contra_mint,
                self.TOKEN_PROGRAM_ID
            )
            target_program = await self.solana_token_program(target_mint)
            target_ata = self.derive_associated_token_account(
                owner,
                target_mint,
                target_program
            )

            ixs = [
                self._create_ata_idempotent_ix(
                    owner,
                    owner,
                    contra_mint,
                    self.TOKEN_PROGRAM_ID,
                    wsol_ata
                ),
                self._create_ata_idempotent_ix(
                    owner,
                    owner,
                    target_mint,
                    target_program,
                    target_ata
                )
            ]
            if wrap_lamports and int(wrap_lamports) > 0:
                ixs.append(
                    transfer(
                        TransferParams(
                            from_pubkey=owner,
                            to_pubkey=wsol_ata,
                            lamports=int(wrap_lamports)
                        )
                    )
                )
                ixs.append(self._sync_native_ix(wsol_ata))
            if delegate_ix:
                ixs.append(self._delegate_ix_from_quote(delegate_ix))

            blockhash = await self.latest_solana_blockhash()
            if not blockhash:
                return {"ok": False, "error": "no_blockhash"}

            message = MessageV0.try_compile(
                owner,
                ixs,
                [],
                Hash.from_string(blockhash)
            )
            vtx = VersionedTransaction(message, [keypair])
            raw_b64 = base64.b64encode(bytes(vtx)).decode()

            sim = await self.solana_rpc(
                "simulateTransaction",
                [
                    raw_b64,
                    {
                        "encoding": "base64",
                        "sigVerify": False,
                        "replaceRecentBlockhash": True,
                        "commitment": "processed"
                    }
                ]
            )
            sim_err = (
                (sim.get("result", {}) or {}).get("value", {}) or {}
            ).get("err")
            if sim_err is not None:
                return {
                    "ok": False,
                    "error": f"setup_simulation_failed:{sim_err}",
                    "raw_response": sim
                }

            if simulate_only:
                return {"ok": True, "simulated": True}

            send = await self.solana_rpc(
                "sendTransaction",
                [
                    raw_b64,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,
                        "preflightCommitment": "confirmed",
                        "maxRetries": 5
                    }
                ]
            )
            signature = send.get("result")
            if not signature:
                return {
                    "ok": False,
                    "error": (
                        (send.get("error") or {}).get("message")
                        or "send_failed"
                    ),
                    "raw_response": send
                }

            return await self.confirm_solana_signature(signature)
        except Exception as exc:
            return {"ok": False, "error": f"onboarding_exception:{exc}"}


class GmgnTradingClient:
    """GMGN execution via the official gmgn-cli `swap` command, trading from
    the wallet bound to the API key (verified: same self-custody funder the
    Flash path used). Buys take an exact lamport amount of SOL; sells take a
    percentage of current holdings — which maps directly onto the engine's
    scale-out/close events. Anti-MEV is on by default."""

    # Canonical wSOL mint (ends ...112). The portfolio API displays a
    # ...111 pseudo-address for native SOL — that one is NOT a real mint and
    # must never be used as a swap leg.
    SOL_TOKEN = "So11111111111111111111111111111111111111112"

    def __init__(self):
        import shutil
        from pathlib import Path

        self._cli = ""
        for candidate in (
            shutil.which("gmgn-cli"),
            str(Path.home() / ".npm-global" / "bin" / "gmgn-cli"),
        ):
            if candidate and Path(candidate).exists():
                self._cli = candidate
                break

    @staticmethod
    def entry_condition_orders():
        """Resting on-chain TP/SL attached at buy time. loss_stop
        price_scale = trigger DROP %, profit_stop price_scale = trigger
        GAIN % (trace variant arms at the gain, sells on drawdown% from
        peak). sell_ratio is % of the bought amount."""

        if not GMGN_TRADING_CONDITION_ORDERS_ENABLED:
            return None

        conditions = []

        if GMGN_TRADING_STOP_LOSS_PCT > 0:
            conditions.append({
                "order_type": "loss_stop",
                "side": "sell",
                "price_scale": decimal_string(
                    GMGN_TRADING_STOP_LOSS_PCT * 100
                ),
                "sell_ratio": "100",
            })

        if GMGN_TRADING_TAKE_PROFIT_MULTIPLE > 1:
            gain_pct = decimal_string(
                (GMGN_TRADING_TAKE_PROFIT_MULTIPLE - 1) * 100
            )
            if GMGN_TRADING_TP_TRAILING_DRAWDOWN_PCT > 0:
                conditions.append({
                    "order_type": "profit_stop_trace",
                    "side": "sell",
                    "price_scale": gain_pct,
                    "sell_ratio": decimal_string(
                        GMGN_TRADING_TAKE_PROFIT_SELL_RATIO_PCT
                    ),
                    "drawdown_rate": decimal_string(
                        GMGN_TRADING_TP_TRAILING_DRAWDOWN_PCT
                    ),
                })
            else:
                conditions.append({
                    "order_type": "profit_stop",
                    "side": "sell",
                    "price_scale": gain_pct,
                    "sell_ratio": decimal_string(
                        GMGN_TRADING_TAKE_PROFIT_SELL_RATIO_PCT
                    ),
                })

        return conditions or None

    @staticmethod
    def response_value(node, *keys):
        if isinstance(node, dict):
            for key in keys:
                if node.get(key) not in (None, ""):
                    return node.get(key)
            for value in node.values():
                found = GmgnTradingClient.response_value(value, *keys)
                if found not in (None, ""):
                    return found
        elif isinstance(node, list):
            for item in node:
                found = GmgnTradingClient.response_value(item, *keys)
                if found not in (None, ""):
                    return found

        return ""

    def configured(self):
        return bool(
            GMGN_API_KEY
            and GMGN_TRADING_WALLET
            and self._cli
        )

    async def _run(self, *args, timeout=45):
        import os as _os

        env = dict(_os.environ, GMGN_API_KEY=GMGN_API_KEY)
        if GMGN_PRIVATE_KEY:
            # strategy list/cancel use signed auth (same funder wallet key)
            env["GMGN_PRIVATE_KEY"] = GMGN_PRIVATE_KEY
        proc = await asyncio.create_subprocess_exec(
            self._cli,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "gmgn_cli_timeout"}

        text = stdout.decode(errors="replace").strip()
        err_text = stderr.decode(errors="replace").strip()
        start = min(
            (i for i in (text.find("{"), text.find("[")) if i >= 0),
            default=-1,
        )

        if start < 0:
            return {
                "ok": False,
                "error": (err_text or text or "gmgn_cli_empty")[:300],
            }

        try:
            data = json.loads(text[start:])
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": f"gmgn_cli_bad_json:{text[start:start + 160]}",
            }

        if isinstance(data, dict) and (
            data.get("error")
            or data.get("err")
            or str(data.get("code", 0)) not in ("0", "None", "")
            and data.get("code") is not None
            and data.get("msg") not in (None, "", "success", "ok")
        ):
            return {
                "ok": False,
                "error": str(
                    data.get("error")
                    or data.get("msg")
                    or data.get("err")
                )[:300],
                "raw_response": data,
            }

        return {"ok": True, "raw_response": data}

    def build_swap_args(
        self,
        *,
        input_token,
        output_token,
        amount=None,
        percent=None,
        condition_orders=None
    ):
        args = [
            "swap",
            "--chain", "sol",
            "--from", GMGN_TRADING_WALLET,
            "--input-token", input_token,
            "--output-token", output_token,
            "--slippage", decimal_string(GMGN_TRADING_SLIPPAGE_PCT),
            "--priority-fee", decimal_string(GMGN_TRADING_PRIORITY_FEE_SOL),
            "--raw",
        ]
        if amount is not None:
            args += ["--amount", str(int(amount))]
        if percent is not None:
            args += ["--percent", decimal_string(percent)]
        if condition_orders:
            args += [
                "--tip-fee",
                decimal_string(GMGN_TRADING_TIP_FEE_SOL),
                "--condition-orders",
                json.dumps(condition_orders, separators=(",", ":")),
                "--sell-ratio-type", "buy_amount",
            ]
        if GMGN_TRADING_ANTI_MEV:
            args.append("--anti-mev")
        return args

    async def swap_buy(self, token, lamports, condition_orders=None):
        return await self._run(*self.build_swap_args(
            input_token=self.SOL_TOKEN,
            output_token=token,
            amount=lamports,
            condition_orders=(
                self.entry_condition_orders()
                if condition_orders is None
                else condition_orders
            ),
        ))

    async def strategy_list_open(self, token=None):
        args = [
            "order", "strategy", "list",
            "--chain", "sol",
            "--type", "open",
            "--group-tag", "STMix",
            "--from", GMGN_TRADING_WALLET,
            "--raw",
        ]
        if token:
            args += ["--base-token", token]
        return await self._run(*args, timeout=25)

    async def strategy_cancel(self, order_id, order_type="smart_trade"):
        return await self._run(
            "order", "strategy", "cancel",
            "--chain", "sol",
            "--from", GMGN_TRADING_WALLET,
            "--order-id", str(order_id),
            "--order-type", order_type,
            "--raw",
            timeout=25,
        )

    async def cancel_open_strategies_for_token(self, token):
        """Best-effort cleanup of resting TP/SL after the bot fully closes a
        position — a zombie TP must not sell tokens from a future re-entry."""

        listing = await self.strategy_list_open(token)
        if not listing.get("ok"):
            return {"ok": False, "error": listing.get("error", "")}

        raw = listing.get("raw_response")
        orders = []

        def collect(node):
            if isinstance(node, dict):
                if node.get("order_id") or node.get("id"):
                    orders.append(node)
                else:
                    for value in node.values():
                        collect(value)
            elif isinstance(node, list):
                for item in node:
                    collect(item)

        collect(raw)
        cancelled = 0
        for order in orders:
            order_id = order.get("order_id") or order.get("id")
            order_type = str(
                order.get("order_type")
                or order.get("group_tag")
                or "smart_trade"
            )
            if "limit" in order_type.lower():
                order_type = "limit_order"
            else:
                order_type = "smart_trade"
            result = await self.strategy_cancel(order_id, order_type)
            if result.get("ok"):
                cancelled += 1

        return {"ok": True, "found": len(orders), "cancelled": cancelled}

    async def swap_sell_percent(self, token, percent):
        return await self._run(*self.build_swap_args(
            input_token=token,
            output_token=self.SOL_TOKEN,
            percent=percent,
        ))

    @staticmethod
    def insufficient_balance_error(error):
        compact = re.sub(r"[^a-z0-9]", "", str(error or "").lower())
        return "insufficientbalance" in compact

    async def token_balance(self, token):
        result = await self._run(
            "portfolio", "token-balance",
            "--chain", "sol",
            "--wallet", GMGN_TRADING_WALLET,
            "--token", token,
            "--raw",
            timeout=20,
        )
        if not result.get("ok"):
            return None
        raw = result.get("raw_response")

        def find_balance(node):
            if isinstance(node, dict):
                for key in ("balance", "amount", "token_balance"):
                    if node.get(key) is not None:
                        return safe_float(node.get(key), None)
                for value in node.values():
                    found = find_balance(value)
                    if found is not None:
                        return found
            if isinstance(node, list):
                for item in node:
                    found = find_balance(item)
                    if found is not None:
                        return found
            return None

        return find_balance(raw)


class LiveExecutionManager:

    def __init__(
        self
    ):

        self.definitive = DefinitiveQuickTradeClient()
        self.flash = DefinitiveFlashClient()
        self.gmgn = GmgnTradingClient()
        self.exit_quote_cache = {}

    def quote_checks_enabled(
        self
    ):

        return (
            LIVE_EXECUTION_ENABLED
            or LIVE_EXECUTION_QUOTE_CHECK_ENABLED
        )

    async def quote_exit_value(
        self,
        *,
        position,
        metrics,
        output_price_usd=0,
        emergency=False
    ):

        if not self.quote_checks_enabled():
            return None

        amount_tokens = safe_float(
            position.get("remaining_tokens"),
            0
        )

        if amount_tokens <= 0:
            return None

        chain = str(
            position.get("chain")
            or getattr(metrics, "chain", "solana")
            or "solana"
        ).lower()
        address = (
            position.get("address")
            or getattr(metrics, "address", "")
        )
        cache_key = (
            chain,
            address,
            round(amount_tokens, 8),
            emergency,
            "definitive"
        )
        cached = self.exit_quote_cache.get(cache_key)
        now = time.time()

        if (
            cached
            and now - cached["timestamp"]
            < max(LIVE_EXECUTION_QUOTE_REFRESH_SECONDS, 0)
        ):
            return dict(
                cached["quote"],
                cached=True
            )

        quote = await self.definitive_exit_quote_value(
            chain=chain,
            address=address,
            amount_tokens=amount_tokens,
            last_price=safe_float(
                position.get("last_price"),
                getattr(metrics, "price", 0)
            ),
            output_price_usd=output_price_usd
        )
        self.exit_quote_cache[cache_key] = {
            "timestamp": now,
            "quote": quote
        }
        return quote

    async def quote_solana_exit_value(
        self,
        *,
        input_mint,
        amount_tokens,
        output_price_usd=0,
        emergency=False
    ):

        return await self.definitive_exit_quote_value(
            chain="solana",
            address=input_mint,
            amount_tokens=amount_tokens,
            last_price=0,
            output_price_usd=output_price_usd
        )

    async def definitive_exit_quote_value(
        self,
        *,
        chain,
        address,
        amount_tokens,
        last_price=0,
        output_price_usd=0
    ):

        if not self.definitive.configured():
            return {
                "quote_available": False,
                "provider": "definitive",
                "error": "definitive_credentials_missing"
            }

        if not self.definitive_chain_allowed(chain):
            return {
                "quote_available": False,
                "provider": "definitive",
                "error": f"chain_not_allowed:{chain}"
            }

        if not address:
            return {
                "quote_available": False,
                "provider": "definitive",
                "error": "missing_quote_asset"
            }

        event = {
            "type": "close",
            "chain": chain,
            "address": address,
            "last_price": last_price,
            "proceeds_usd": safe_float(last_price, 0) * amount_tokens,
            "live_execution_sell_tokens": amount_tokens
        }
        body = (
            self.definitive_trade_order_body(
                event,
                "sell",
                amount_tokens
            )
            if DEFINITIVE_SELL_ORDER_ENDPOINT == "trade"
            else self.definitive_order_body(
                event,
                "sell",
                amount_tokens
            )
        )
        quote = (
            await self.definitive.trade_quote(body)
            if DEFINITIVE_SELL_ORDER_ENDPOINT == "trade"
            else await self.definitive.quicktrade_quote(body)
        )

        if not quote.get("ok"):
            return {
                "quote_available": False,
                "provider": "definitive",
                "attempt_name": "definitive",
                "error": quote.get("error", "definitive_quote_failed"),
                "status": quote.get("status"),
                "raw_quote": quote.get("raw_response", {})
            }

        blocked, reason = self.definitive_quote_blocks_submit(
            quote,
            side="sell"
        )

        if blocked:
            return {
                "quote_available": False,
                "provider": "definitive",
                "attempt_name": "definitive",
                "error": reason,
                "status": quote.get("status"),
                "raw_quote": quote.get("raw_response", {})
            }

        return self.definitive_exit_quote_result(
            quote,
            body=body,
            amount_tokens=amount_tokens,
            output_price_usd=output_price_usd
        )

    def definitive_exit_quote_result(
        self,
        quote,
        *,
        body,
        amount_tokens,
        output_price_usd=0
    ):

        output_mint = body.get("contraAsset", "")
        output_amount = self.quote_numeric_field(
            quote,
            "contraAssetAmount",
            "contraAmount",
            "amountOut",
            "quotedAmountOut",
            "outputAmount",
            "toAmount",
            "expectedContraAmount",
            "expectedOutputAmount"
        )
        min_output_amount = self.quote_numeric_field(
            quote,
            "minAmountOut",
            "minOutputAmount",
            "minimumAmountOut",
            "minContraAssetAmount"
        )
        avg_price = self.quote_numeric_field(
            quote,
            "averageNotionalPrice",
            "averagePrice",
            "notionalPrice",
            "assetPrice",
            "price"
        )

        if output_amount <= 0 and avg_price > 0:
            output_amount = amount_tokens * avg_price

        quote_value_usd = self.quote_numeric_field(
            quote,
            "toNotional",
            "outputNotional",
            "contraNotional",
            "expectedToNotional"
        )

        if quote_value_usd <= 0:
            quote_value_usd = quote_output_value_usd(
                output_mint,
                output_amount,
                output_price_usd=output_price_usd
            )

        min_quote_value_usd = self.quote_numeric_field(
            quote,
            "minAmountOutNotional",
            "minOutputNotional",
            "minToNotional",
            "minimumToNotional"
        )

        if quote_value_usd <= 0 and avg_price > 0:
            quote_value_usd = amount_tokens * avg_price

        if min_quote_value_usd <= 0 and min_output_amount > 0:
            min_quote_value_usd = quote_output_value_usd(
                output_mint,
                min_output_amount,
                output_price_usd=output_price_usd
            )

        if min_output_amount <= 0:
            min_output_amount = output_amount

        if min_quote_value_usd <= 0:
            min_quote_value_usd = quote_value_usd

        return {
            "quote_available": quote_value_usd > 0,
            "provider": "definitive",
            "attempt_name": "definitive",
            "input_mint": body.get("targetAsset", ""),
            "input_amount_tokens": amount_tokens,
            "output_mint": output_mint,
            "output_amount": output_amount,
            "quote_value_usd": quote_value_usd,
            "min_output_amount": min_output_amount,
            "min_quote_value_usd": min_quote_value_usd,
            "price_impact_pct": self.quote_numeric_field(
                quote,
                "estimatedPriceImpact",
                "priceImpact",
                "priceImpactPct",
                "priceImpactPercent",
                "quotedPriceImpact"
            ),
            "route": "definitive",
            "raw_quote": quote.get("raw_response", {}),
            "error": "" if quote_value_usd > 0 else "definitive_quote_missing_value"
        }

    def definitive_ordering_enabled(
        self
    ):

        return (
            LIVE_EXECUTION_ENABLED
            and DEFINITIVE_EXECUTION_ENABLED
        )

    def definitive_live_submit_enabled(
        self
    ):

        return (
            self.definitive_ordering_enabled()
            and not LIVE_EXECUTION_DRY_RUN
            and DEFINITIVE_EXECUTION_CONFIRM_LIVE
        )

    def ordering_enabled(
        self
    ):

        return (
            self.gmgn_trading_enabled()
            or self.flash_ordering_enabled()
            or self.definitive_ordering_enabled()
        )

    def preferred_live_provider(
        self
    ):

        if self.gmgn_trading_enabled():
            return "gmgn"
        if self.flash_ordering_enabled():
            return "flash"
        if self.definitive_ordering_enabled():
            return "definitive"
        return "disabled"

    def definitive_chain_allowed(
        self,
        chain
    ):

        chain = str(
            chain or "solana"
        ).lower()
        allowed = tuple(
            str(item or "").lower()
            for item in DEFINITIVE_ALLOWED_CHAINS
        )

        return (
            "*" in allowed
            or chain in allowed
        )

    def definitive_contra_asset(
        self,
        chain
    ):

        chain = str(
            chain or "solana"
        ).lower()
        mapping = {
            "solana": DEFINITIVE_SOLANA_CONTRA_ASSET,
            "ethereum": DEFINITIVE_ETHEREUM_CONTRA_ASSET,
            "base": DEFINITIVE_BASE_CONTRA_ASSET,
            "bsc": DEFINITIVE_BSC_CONTRA_ASSET,
            "hyperevm": DEFINITIVE_HYPEREVM_CONTRA_ASSET,
            "hyperliquid": DEFINITIVE_HYPEREVM_CONTRA_ASSET
        }

        return (
            mapping.get(chain)
            or DEFINITIVE_DEFAULT_CONTRA_ASSET
        )

    def definitive_uses_solana_sol_contra(
        self,
        chain
    ):

        return (
            str(chain or "").lower() == "solana"
            and str(
                self.definitive_contra_asset(chain)
                or ""
            ) == SOL_MINT
        )

    def definitive_contra_asset_price_usd(
        self,
        event,
        chain
    ):

        chain = str(
            chain or "solana"
        ).lower()
        contra_asset = str(
            self.definitive_contra_asset(chain)
            or ""
        ).lower()

        if contra_asset == USDC_MINT.lower():
            return 1.0

        if (
            chain == "solana"
            and contra_asset == SOL_MINT.lower()
        ):
            return self.event_contra_asset_usd_price(event)

        return safe_float(
            event.get("contra_asset_price_usd"),
            0
        )

    def event_contra_asset_usd_price(
        self,
        event
        ):

        for key in (
            "contra_asset_usd",
            "sol_usd",
            "entry_sol_usd"
        ):
            value = safe_float(
                event.get(key),
                0
            )

            if value > 0:
                return value

        entry_notional = safe_float(
            event.get("entry_notional_usd"),
            0
        )
        entry_size_sol = safe_float(
            event.get("entry_size_sol"),
            0
        )

        if entry_notional > 0 and entry_size_sol > 0:
            return entry_notional / entry_size_sol

        return 0

    def definitive_body_qty(
        self,
        event,
        side,
        qty,
        chain
    ):

        qty = safe_float(
            qty,
            0
        )

        details = {
            "body_qty": qty,
            "order_value_usd": 0,
            "contra_asset_price_usd": 0,
            "error": ""
        }

        if side != "buy":
            details["order_value_usd"] = safe_float(
                event.get("proceeds_usd"),
                0
            )
            return details

        details["order_value_usd"] = qty

        if not self.definitive_uses_solana_sol_contra(chain):
            return details

        contra_asset_usd = self.event_contra_asset_usd_price(
            event
        )

        if contra_asset_usd <= 0:
            details["body_qty"] = 0
            details["error"] = "contra_asset_usd_price_missing_for_contra_qty"
            return details

        details["body_qty"] = qty / contra_asset_usd
        details["contra_asset_price_usd"] = contra_asset_usd
        return details

    async def solana_sol_balance(
        self,
        address
    ):

        urls = solana_rpc_urls()

        if not urls or not address:
            return None
        rpc_url = urls[0]

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [address]
        }
        timeout = aiohttp.ClientTimeout(total=10)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(rpc_url, json=payload) as response:
                    data = await response.json(content_type=None)
        except Exception:
            return None

        lamports = safe_float(
            (
                data.get("result", {})
                .get("value")
            ),
            -1
        )

        if lamports < 0:
            return None

        return lamports / 1_000_000_000

    async def solana_token_raw_balance(
        self,
        owner,
        mint
    ):

        urls = solana_rpc_urls()

        if not urls or not owner or not mint:
            return {
                "ok": False,
                "raw_balance": 0,
                "decimals": 0,
                "error": "missing_solana_rpc_or_balance_params"
            }
        rpc_url = urls[0]

        total = Decimal(0)
        decimals = 0
        seen = set()
        filters = [
            {"mint": mint},
            {"programId": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"},
        ]
        timeout = aiohttp.ClientTimeout(total=20)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for account_filter in filters:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        owner,
                        account_filter,
                        {"encoding": "jsonParsed"}
                    ]
                }
                async with session.post(rpc_url, json=payload) as response:
                    data = await response.json(content_type=None)

                if data.get("error"):
                    return {
                        "ok": False,
                        "raw_balance": 0,
                        "decimals": 0,
                        "error": data["error"].get(
                            "message",
                            str(data["error"])
                        )
                    }

                for item in (
                    data.get("result", {}).get("value", [])
                    if isinstance(data, dict)
                    else []
                ):
                    pubkey = item.get("pubkey", "")

                    if pubkey in seen:
                        continue

                    seen.add(pubkey)
                    info = (
                        item.get("account", {})
                        .get("data", {})
                        .get("parsed", {})
                        .get("info", {})
                    )

                    if info.get("mint") != mint:
                        continue

                    token_amount = info.get("tokenAmount", {})
                    total += Decimal(
                        str(token_amount.get("amount", "0"))
                    )
                    decimals = safe_int(
                        token_amount.get("decimals"),
                        decimals
                    )

        return {
            "ok": True,
            "raw_balance": int(total),
            "decimals": decimals,
            "error": ""
        }

    def quote_numeric_field(
        self,
        quote,
        *names
    ):

        raw = quote.get("raw_response", {})
        candidates = [quote]

        if isinstance(raw, dict):
            candidates.extend([
                raw,
                raw.get("data", {}),
                raw.get("quote", {}),
                raw.get("result", {}),
                raw.get("metadata", {})
            ])
            quote_obj = raw.get("quote", {})
            if isinstance(quote_obj, dict):
                candidates.extend([
                    quote_obj.get("quote", {}),
                    quote_obj.get("data", {}),
                    quote_obj.get("metadata", {}),
                    quote_obj.get("result", {})
                ])

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            for name in names:
                if name in candidate:
                    value = safe_float(
                        candidate.get(name),
                        0
                    )

                    if value:
                        return value

        return 0

    def quote_warnings(
        self,
        quote
    ):

        raw = quote.get("raw_response", {})
        warnings = []

        for candidate in (
            raw,
            raw.get("data", {}) if isinstance(raw, dict) else {},
            raw.get("quote", {}) if isinstance(raw, dict) else {},
            (
                raw.get("quote", {}).get("quote", {})
                if isinstance(raw, dict)
                and isinstance(raw.get("quote", {}), dict)
                else {}
            ),
            raw.get("metadata", {}) if isinstance(raw, dict) else {}
        ):
            if not isinstance(candidate, dict):
                continue

            value = candidate.get("warnings")

            if isinstance(value, list):
                warnings.extend(
                    str(item)
                    for item in value
                    if str(item)
                )
            elif value:
                warnings.append(str(value))

        return warnings

    def quote_bool_field(
        self,
        quote,
        *names
    ):

        raw = quote.get("raw_response", {})
        candidates = [quote]

        if isinstance(raw, dict):
            candidates.extend([
                raw,
                raw.get("data", {}),
                raw.get("quote", {}),
                raw.get("result", {}),
                raw.get("metadata", {})
            ])
            quote_obj = raw.get("quote", {})
            if isinstance(quote_obj, dict):
                candidates.extend([
                    quote_obj.get("quote", {}),
                    quote_obj.get("data", {}),
                    quote_obj.get("metadata", {}),
                    quote_obj.get("result", {})
                ])

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            for name in names:
                if name not in candidate:
                    continue

                value = candidate.get(name)

                if isinstance(value, bool):
                    return value

                text = str(value).strip().lower()

                if text in ("true", "1", "yes"):
                    return True

                if text in ("false", "0", "no"):
                    return False

        return None

    def definitive_entry_notional_usd(
        self,
        event,
        open_summary=None
    ):

        position_notional = safe_float(
            event.get("entry_notional_usd"),
            0
        )
        requested = (
            position_notional
            if DEFINITIVE_MIRROR_PAPER_POSITION_SIZE
            else DEFINITIVE_MAX_ENTRY_NOTIONAL_USD
        )

        if DEFINITIVE_MAX_ENTRY_NOTIONAL_USD > 0:
            requested = min(
                requested,
                DEFINITIVE_MAX_ENTRY_NOTIONAL_USD
            )

        summary = open_summary or {}
        exposure_limit = safe_float(
            DEFINITIVE_MAX_ACCOUNT_EXPOSURE_USD,
            0
        )

        if exposure_limit > 0:
            available = (
                exposure_limit
                - safe_float(
                    summary.get("open_exposure_usd"),
                    0
                )
            )
            requested = min(
                requested,
                max(available, 0)
            )

        return requested

    def definitive_order_body(
        self,
        event,
        side,
        qty
    ):

        chain = str(
            event.get("chain", "solana")
            or "solana"
        ).lower()
        body = {
            "chain": chain,
            "targetAsset": event.get("address"),
            "contraAsset": self.definitive_contra_asset(chain),
            "qty": decimal_string(qty),
            "orderSide": side,
            "slippageTolerance": DEFINITIVE_SLIPPAGE_TOLERANCE,
            "secondsToExpire": self.definitive_seconds_to_expire(
                side
            )
        }

        if DEFINITIVE_USE_DISPLAY_ASSET_PRICE:
            display_price = safe_float(
                event.get("last_price"),
                event.get("entry_price") or 0
            )

            if display_price > 0:
                body["displayAssetPrice"] = decimal_string(
                    display_price,
                    places=18
                )

        return body

    def definitive_trade_order_body(
        self,
        event,
        side,
        qty
    ):

        body = self.definitive_order_body(
            event,
            side,
            qty
        )
        body["type"] = "market"
        body["maxPriceImpact"] = str(
            self.definitive_max_price_impact(
                side
            )
        )
        body.pop(
            "secondsToExpire",
            None
        )
        return body

    def definitive_seconds_to_expire(
        self,
        side
    ):

        if side == "buy":
            return max(
                safe_int(
                    DEFINITIVE_ENTRY_SECONDS_TO_EXPIRE,
                    DEFINITIVE_SECONDS_TO_EXPIRE
                ),
                1
            )

        return max(
            safe_int(
                DEFINITIVE_SECONDS_TO_EXPIRE,
                0
            ),
            1
        )

    def definitive_max_price_impact(
        self,
        side
    ):

        configured = (
            DEFINITIVE_EXIT_MAX_PRICE_IMPACT
            if side == "sell"
            else DEFINITIVE_ENTRY_MAX_PRICE_IMPACT
        )

        return safe_float(
            configured,
            DEFINITIVE_MAX_PRICE_IMPACT
        )

    def definitive_quote_id(
        self,
        quote
    ):

        raw = (
            quote.get("raw_response", {})
            if isinstance(quote, dict)
            else {}
        )

        if not isinstance(raw, dict):
            return ""

        quote_obj = raw.get("quote", {})

        if not isinstance(quote_obj, dict):
            quote_obj = {}

        nested_quote = quote_obj.get("quote", {})

        if not isinstance(nested_quote, dict):
            nested_quote = {}

        return (
            nested_quote.get("id")
            or quote_obj.get("id")
            or quote_obj.get("quoteId")
            or raw.get("quoteId")
            or ""
        )

    def definitive_order_id_from_response(
        self,
        response
    ):

        raw = (
            response.get("raw_response", {})
            if isinstance(response, dict)
            else response
        )

        if not isinstance(raw, dict):
            return ""

        raw_order = raw.get("order", {})

        if not isinstance(raw_order, dict):
            raw_order = {}

        return (
            raw.get("orderId")
            or raw.get("id")
            or raw_order.get("orderId")
            or raw_order.get("id")
            or ""
        )

    def definitive_confirmed_order_raw(
        self,
        response
    ):

        raw = (
            response.get("raw_response", {})
            if isinstance(response, dict)
            else response
        )

        if not isinstance(raw, dict):
            return {}

        confirmed = raw.get("confirmed_order_detail")

        if isinstance(confirmed, dict) and confirmed:
            return confirmed

        return raw

    def definitive_order_from_response(
        self,
        response
    ):

        raw = self.definitive_confirmed_order_raw(
            response
        )

        if not isinstance(raw, dict):
            return {}

        raw_order = raw.get("order", {})

        if isinstance(raw_order, dict):
            return raw_order

        return raw

    def definitive_order_filled_amounts(
        self,
        response
    ):

        order = self.definitive_order_from_response(
            response
        )
        filled = order.get("filled", {})

        if not isinstance(filled, dict):
            filled = {}

        return {
            "target": safe_float(
                filled.get("targetAssetAmount"),
                0
            ),
            "contra": safe_float(
                filled.get("contraAssetAmount"),
                0
            ),
            "average_price": safe_float(
                filled.get("averagePrice"),
                0
            ),
            "average_notional_price": safe_float(
                filled.get("averageNotionalPrice"),
                0
            )
        }

    def definitive_with_confirmed_order_detail(
        self,
        submit,
        detail
    ):

        if not isinstance(submit, dict):
            return submit

        if not isinstance(detail, dict) or not detail.get("ok"):
            return submit

        confirmed = dict(submit)
        raw = submit.get("raw_response", {})

        if isinstance(raw, dict):
            raw = dict(raw)
        else:
            raw = {}

        raw["confirmed_order_detail"] = detail.get(
            "raw_response",
            {}
        )
        confirmed["raw_response"] = raw
        return confirmed

    def definitive_order_close_reason(
        self,
        detail
    ):

        raw_order = self.definitive_order_from_response(
            detail
        )

        return (
            raw_order.get("closeReason")
            or raw_order.get("reason")
            or ""
        )

    def definitive_order_status(
        self,
        detail
    ):

        raw_order = self.definitive_order_from_response(
            detail
        )

        return (
            raw_order.get("status")
            or ""
        )

    async def definitive_wait_for_terminal_order(
        self,
        order_id,
        timeout_seconds
    ):

        deadline = time.time() + max(
            safe_float(timeout_seconds, 0),
            0
        )
        terminal_statuses = {
            "ORDER_STATUS_FILLED",
            "ORDER_STATUS_CANCELLED",
            "ORDER_STATUS_REJECTED",
            "ORDER_STATUS_TERMINATED"
        }
        last_detail = None

        while time.time() <= deadline:
            detail = await self.definitive.request(
                "GET",
                f"/orders/{order_id}"
            )

            if detail.get("ok"):
                last_detail = detail
                status = self.definitive_order_status(
                    detail
                )

                if status in terminal_statuses:
                    return detail

            await asyncio.sleep(2)

        return last_detail

    async def definitive_confirm_submit(
        self,
        submit,
        timeout_seconds,
        *,
        require_filled=False
    ):

        order_id = self.definitive_order_id_from_response(
            submit
        )

        if not order_id:
            return submit

        detail = await self.definitive_wait_for_terminal_order(
            order_id,
            timeout_seconds
        )

        if not detail or not detail.get("ok"):
            return submit

        status = self.definitive_order_status(
            detail
        )

        if status == "ORDER_STATUS_FILLED":
            return self.definitive_with_confirmed_order_detail(
                submit,
                detail
            )

        if status in (
            "ORDER_STATUS_CANCELLED",
            "ORDER_STATUS_REJECTED",
            "ORDER_STATUS_TERMINATED"
        ):
            reason = (
                self.definitive_order_close_reason(detail)
                or status
            )
            return {
                "ok": False,
                "provider": "definitive",
                "status": detail.get("status"),
                "error": reason,
                "raw_response": detail.get("raw_response", {})
            }

        if require_filled:
            return {
                "ok": False,
                "provider": "definitive",
                "status": detail.get("status"),
                "error": "definitive_order_not_filled_before_timeout",
                "raw_response": detail.get("raw_response", {})
            }

        return self.definitive_with_confirmed_order_detail(
            submit,
            detail
        )

    async def definitive_trade_submit_from_quote(
        self,
        body,
        quote
    ):

        quote_id = self.definitive_quote_id(
            quote
        )

        if not quote_id:
            return {
                "ok": False,
                "provider": "definitive",
                "error": "definitive_quote_id_missing",
                "raw_response": (
                    quote.get("raw_response")
                    if isinstance(quote, dict)
                    else {}
                )
            }

        raw = quote.get("raw_response", {})
        submit_bodies = []

        if (
            isinstance(raw, dict)
            and isinstance(raw.get("quote"), dict)
        ):
            submit_bodies.append(raw)

        submit_bodies.append({
            "externalOrderRequest": body,
            "quoteId": quote_id
        })

        first_submit = None
        seen = set()

        for submit_body in submit_bodies:
            key = compact_json(submit_body)

            if key in seen:
                continue

            seen.add(key)
            submit = await self.definitive.trade_submit(
                submit_body
            )

            if first_submit is None:
                first_submit = submit

            if submit.get("ok"):
                return submit

        return first_submit or {
            "ok": False,
            "provider": "definitive",
            "error": "definitive_trade_submit_failed_without_attempt"
        }

    async def definitive_confirm_sell_submit(
        self,
        submit
    ):

        return await self.definitive_confirm_submit(
            submit,
            DEFINITIVE_SELL_CONFIRM_FILL_SECONDS
        )

    def definitive_retryable_submit_error(
        self,
        reason
    ):

        reason = str(reason or "").strip()

        if not reason:
            return False

        retryable_terms = (
            "REASON_PRICE_IMPACT_TOO_HIGH",
            "REASON_SLIPPAGE_TOO_HIGH",
            "REASON_MARKETABILITY_FAILED",
            "ORDER_STATUS_CANCELLED",
            "ORDER_STATUS_REJECTED",
            "ORDER_STATUS_TERMINATED",
            "definitive_order_not_filled_before_timeout"
        )

        return any(
            term in reason
            for term in retryable_terms
        )

    def definitive_quote_blocks_submit(
        self,
        quote,
        *,
        side="buy"
    ):

        if not quote.get("ok"):
            return (
                True,
                quote.get("error", "definitive_quote_failed")
            )

        is_marketable = self.quote_bool_field(
            quote,
            "isMarketable"
        )

        if is_marketable is False:
            return (
                True,
                "definitive_quote_not_marketable"
            )

        price_impact = self.quote_numeric_field(
            quote,
            "estimatedPriceImpact",
            "priceImpact",
            "priceImpactPct",
            "priceImpactPercent",
            "quotedPriceImpact"
        )

        max_price_impact = self.definitive_max_price_impact(
            side
        )

        if (
            max_price_impact > 0
            and price_impact > max_price_impact
        ):
            return (
                True,
                "definitive_price_impact_above_max"
            )

        expected_slippage = self.quote_numeric_field(
            quote,
            "expectedSlippage"
        )
        slippage_limit = safe_float(
            DEFINITIVE_SLIPPAGE_TOLERANCE,
            0
        )

        if (
            slippage_limit > 0
            and expected_slippage > slippage_limit
        ):
            return (
                True,
                "definitive_expected_slippage_above_max"
            )

        warnings = self.quote_warnings(quote)

        if (
            DEFINITIVE_ABORT_ON_QUOTE_WARNINGS
            and warnings
        ):
            return (
                True,
                "definitive_quote_warning"
            )

        return (
            False,
            ""
        )

    def definitive_submit_result(
        self,
        *,
        event,
        side,
        qty,
        body,
        order_qty=None,
        order_value_usd=0,
        contra_asset_price_usd=0,
        quote=None,
        submit=None,
        skipped=False,
        reason=""
    ):

        order_id = self.definitive_order_id_from_response(
            submit
        )
        filled_amounts = self.definitive_order_filled_amounts(
            submit
        )

        return {
            "enabled": self.definitive_ordering_enabled(),
            "provider": "definitive",
            "event_type": event.get("type", ""),
            "chain": body.get("chain", ""),
            "side": side,
            "qty": decimal_string(qty),
            "order_qty": (
                body.get("qty", "")
                if order_qty is None
                else decimal_string(order_qty)
            ),
            "order_value_usd": safe_float(
                order_value_usd,
                0
            ),
            "contra_asset_price_usd": safe_float(
                contra_asset_price_usd,
                0
            ),
            "target_asset": body.get("targetAsset", ""),
            "contra_asset": body.get("contraAsset", ""),
            "dry_run": not self.definitive_live_submit_enabled(),
            "accepted": bool(order_id),
            "submitted": bool(
                submit
                and submit.get("ok")
            ),
            "skipped": bool(skipped),
            "reconciled": bool(
                isinstance(submit, dict)
                and submit.get("reconciled")
            ),
            "reason": reason,
            "order_id": order_id,
            "filled_target_amount": filled_amounts.get(
                "target",
                0
            ),
            "filled_contra_amount": filled_amounts.get(
                "contra",
                0
            ),
            "average_fill_price": filled_amounts.get(
                "average_price",
                0
            ),
            "average_notional_price": filled_amounts.get(
                "average_notional_price",
                0
            ),
            "quote_ok": bool(
                quote
                and quote.get("ok")
            ),
            "quote_status": (
                quote.get("status")
                if isinstance(quote, dict)
                else None
            ),
            "quote_error": (
                quote.get("error", "")
                if isinstance(quote, dict)
                else ""
            ),
            "quote_price_impact": (
                self.quote_numeric_field(
                    quote or {},
                    "estimatedPriceImpact",
                    "priceImpact",
                    "priceImpactPct",
                    "priceImpactPercent"
                )
                if quote
                else 0
            ),
            "submit_status": (
                submit.get("status")
                if isinstance(submit, dict)
                else None
            ),
            "submit_error": (
                submit.get("error", "")
                if isinstance(submit, dict)
                else ""
            )
        }

    async def execute_definitive_position_event(
        self,
        event,
        *,
        open_summary=None,
        has_live_position=False
    ):

        event = event or {}
        event_type = event.get("type", "")

        if event_type not in (
            "entry",
            "scale_out",
            "live_scale_out",
            "close"
        ):
            return {
                "enabled": self.definitive_ordering_enabled(),
                "provider": "definitive",
                "skipped": True,
                "reason": "unsupported_event_type",
                "event_type": event_type
            }

        if not self.definitive_ordering_enabled():
            return {
                "enabled": False,
                "provider": "definitive",
                "skipped": True,
                "reason": "definitive_execution_disabled",
                "event_type": event_type
            }

        if not self.definitive.configured():
            return {
                "enabled": True,
                "provider": "definitive",
                "skipped": True,
                "reason": "definitive_credentials_missing",
                "event_type": event_type
            }

        chain = str(
            event.get("chain", "solana")
            or "solana"
        ).lower()

        if not self.definitive_chain_allowed(chain):
            return {
                "enabled": True,
                "provider": "definitive",
                "skipped": True,
                "reason": f"chain_not_allowed:{chain}",
                "event_type": event_type
            }

        if not self.definitive_contra_asset(chain):
            return {
                "enabled": True,
                "provider": "definitive",
                "skipped": True,
                "reason": f"contra_asset_missing:{chain}",
                "event_type": event_type
            }

        if event_type == "entry":
            summary = open_summary or {}

            if (
                DEFINITIVE_MAX_OPEN_POSITIONS > 0
                and safe_int(
                    summary.get("open_count"),
                    0
                )
                >= DEFINITIVE_MAX_OPEN_POSITIONS
            ):
                return {
                    "enabled": True,
                    "provider": "definitive",
                    "skipped": True,
                    "reason": "definitive_max_open_positions",
                    "event_type": event_type
                }

            qty = self.definitive_entry_notional_usd(
                event,
                open_summary=summary
            )

            if qty < DEFINITIVE_MIN_ENTRY_NOTIONAL_USD:
                return {
                    "enabled": True,
                    "provider": "definitive",
                    "skipped": True,
                    "reason": "entry_notional_below_min_or_exposure_full",
                    "event_type": event_type,
                    "qty": decimal_string(qty)
                }

            side = "buy"
        else:
            if not has_live_position:
                return {
                    "enabled": True,
                    "provider": "definitive",
                    "skipped": True,
                    "reason": "no_live_entry_for_position",
                    "event_type": event_type
                }

            price = safe_float(
                event.get("last_price"),
                0
            )
            qty = safe_float(
                event.get("live_execution_sell_tokens"),
                0
            )

            if qty <= 0:
                qty = (
                    safe_float(
                        event.get("proceeds_usd"),
                        0
                    )
                    / max(price, 1e-18)
                )

            remaining_tokens = safe_float(
                event.get("live_execution_remaining_tokens_estimated"),
                0
            )

            if remaining_tokens > 0:
                qty = min(
                    qty,
                    remaining_tokens
                )

            if qty <= 0:
                return {
                    "enabled": True,
                    "provider": "definitive",
                    "skipped": True,
                    "reason": "zero_exit_quantity",
                    "event_type": event_type
                }

            side = "sell"

        qty_details = self.definitive_body_qty(
            event,
            side,
            qty,
            chain
        )
        body_qty = safe_float(
            qty_details.get("body_qty"),
            0
        )

        if body_qty <= 0:
            return {
                "enabled": True,
                "provider": "definitive",
                "skipped": True,
                "reason": (
                    qty_details.get("error")
                    or "zero_order_quantity"
                ),
                "event_type": event_type,
                "side": side,
                "qty": decimal_string(qty),
                "order_qty": "0",
                "target_asset": event.get("address", ""),
                "contra_asset": self.definitive_contra_asset(chain),
                "order_value_usd": safe_float(
                    qty_details.get("order_value_usd"),
                    0
                ),
                "contra_asset_price_usd": safe_float(
                    qty_details.get("contra_asset_price_usd"),
                    0
                )
            }

        use_trade_endpoint = (
            side == "sell"
            and DEFINITIVE_SELL_ORDER_ENDPOINT == "trade"
        )
        max_attempts = max(
            safe_int(
                DEFINITIVE_SUBMIT_MAX_ATTEMPTS,
                1
            ),
            1
        )
        retry_delay = max(
            safe_float(
                DEFINITIVE_SUBMIT_RETRY_DELAY_SECONDS,
                0
            ),
            0
        )
        last_result = None

        for attempt in range(max_attempts):
            body = (
                self.definitive_trade_order_body(
                    event,
                    side,
                    body_qty
                )
                if use_trade_endpoint
                else self.definitive_order_body(
                    event,
                    side,
                    body_qty
                )
            )
            quote = None

            if DEFINITIVE_QUOTE_BEFORE_SUBMIT or use_trade_endpoint:
                quote = (
                    await self.definitive.trade_quote(
                        body
                    )
                    if use_trade_endpoint
                    else await self.definitive.quicktrade_quote(
                        body
                    )
                )
                blocked, reason = self.definitive_quote_blocks_submit(
                    quote,
                    side=side
                )

                if blocked:
                    return self.definitive_submit_result(
                        event=event,
                        side=side,
                        qty=qty,
                        body=body,
                        order_qty=body_qty,
                        order_value_usd=qty_details.get("order_value_usd", 0),
                        contra_asset_price_usd=qty_details.get(
                            "contra_asset_price_usd",
                            0
                        ),
                        quote=quote,
                        skipped=True,
                        reason=reason
                    )

            if not self.definitive_live_submit_enabled():
                return self.definitive_submit_result(
                    event=event,
                    side=side,
                    qty=qty,
                    body=body,
                    order_qty=body_qty,
                    order_value_usd=qty_details.get("order_value_usd", 0),
                    contra_asset_price_usd=qty_details.get(
                        "contra_asset_price_usd",
                        0
                    ),
                    quote=quote,
                    skipped=True,
                    reason="live_submit_not_armed"
                )

            submit = (
                await self.definitive_trade_submit_from_quote(
                    body,
                    quote or {}
                )
                if use_trade_endpoint
                else await self.definitive.quicktrade_submit(
                    body
                )
            )

            if (
                use_trade_endpoint
                and submit.get("ok")
            ):
                submit = await self.definitive_confirm_sell_submit(
                    submit
                )

            last_result = self.definitive_submit_result(
                event=event,
                side=side,
                qty=qty,
                body=body,
                order_qty=body_qty,
                order_value_usd=qty_details.get("order_value_usd", 0),
                contra_asset_price_usd=qty_details.get(
                    "contra_asset_price_usd",
                    0
                ),
                quote=quote,
                submit=submit,
                skipped=not submit.get("ok"),
                reason=submit.get("error", "")
            )

            submit_reason = (
                last_result.get("submit_error")
                or last_result.get("reason")
                or ""
            )

            if (
                last_result.get("submitted")
                or attempt + 1 >= max_attempts
                or not self.definitive_retryable_submit_error(
                    submit_reason
                )
            ):
                return last_result

            if retry_delay > 0:
                await asyncio.sleep(retry_delay)

        return last_result or {
            "enabled": True,
            "provider": "definitive",
            "submitted": False,
            "skipped": True,
            "reason": "definitive_submit_failed_without_result"
        }

    def gmgn_trading_enabled(
        self
    ):

        return (
            LIVE_EXECUTION_ENABLED
            and GMGN_TRADING_ENABLED
        )

    def gmgn_live_submit_enabled(
        self
    ):

        return (
            self.gmgn_trading_enabled()
            and not LIVE_EXECUTION_DRY_RUN
            and GMGN_TRADING_CONFIRM_LIVE
        )

    def gmgn_submit_result(
        self,
        *,
        event,
        side,
        qty,
        target_asset,
        order_id="",
        filled_target=0,
        filled_contra=0,
        submitted=False,
        skipped=False,
        reason="",
        entry_notional_usd=None,
        strategy_order_id="",
        condition_order_count=0
    ):

        result = {
            "enabled": self.gmgn_trading_enabled(),
            "provider": "gmgn",
            "event_type": event.get("type", ""),
            "chain": "solana",
            "side": side,
            "qty": decimal_string(qty),
            "target_asset": target_asset,
            "contra_asset": GmgnTradingClient.SOL_TOKEN,
            "dry_run": not self.gmgn_live_submit_enabled(),
            "accepted": bool(order_id) or submitted,
            "submitted": bool(submitted),
            "skipped": bool(skipped),
            "reason": reason,
            "order_id": order_id,
            "filled_target_amount": filled_target,
            "filled_contra_amount": filled_contra,
            "strategy_order_id": strategy_order_id,
            "condition_order_count": condition_order_count,
        }
        if entry_notional_usd is not None:
            result["entry_notional_usd"] = entry_notional_usd
        return result

    def flash_ordering_enabled(
        self
    ):

        return (
            LIVE_EXECUTION_ENABLED
            and DEFINITIVE_FLASH_ENABLED
        )

    def flash_live_submit_enabled(
        self
    ):

        return (
            self.flash_ordering_enabled()
            and not LIVE_EXECUTION_DRY_RUN
            and DEFINITIVE_EXECUTION_CONFIRM_LIVE
            and DEFINITIVE_FLASH_CONFIRM_LIVE
        )

    def flash_order_id_from_response(
        self,
        response
    ):

        raw = (
            response.get("raw_response", {})
            if isinstance(response, dict)
            else {}
        )

        if not isinstance(raw, dict):
            return ""

        return raw.get("orderId") or raw.get("id") or ""

    def flash_order_status(
        self,
        detail
    ):

        raw = (
            detail.get("raw_response", {})
            if isinstance(detail, dict)
            else {}
        )

        if not isinstance(raw, dict):
            return ""

        order = raw.get("order", {})

        if isinstance(order, dict):
            return order.get("status") or raw.get("status") or ""

        return raw.get("status") or ""

    def flash_normalized_order_status(
        self,
        detail
    ):

        status = str(
            self.flash_order_status(detail)
            or ""
        ).upper()

        if status.startswith("ORDER_STATUS_"):
            status = status.replace("ORDER_STATUS_", "", 1)

        return status

    def flash_order_close_reason(
        self,
        detail
    ):

        # FlashOrder.closeReason (e.g. REASON_USER_REQUESTED,
        # REASON_FULLY_FILLED, REASON_ORDER_EXPIRED) — null while active.
        # Definitive confirmed (2026-06-11) this is the field that explains
        # server-side cancellations, so every terminal result carries it.
        raw = (
            detail.get("raw_response", {})
            if isinstance(detail, dict)
            else {}
        )

        if not isinstance(raw, dict):
            return ""

        order = raw.get("order", {})

        if isinstance(order, dict) and order.get("closeReason"):
            return str(order.get("closeReason"))

        return str(raw.get("closeReason") or "")

    def flash_order_filled_amounts(
        self,
        detail
    ):

        raw = (
            detail.get("raw_response", {})
            if isinstance(detail, dict)
            else {}
        )

        if not isinstance(raw, dict):
            raw = {}

        # Flash fill amounts use targetAmount/contraAmount on each FlashFill and
        # on the aggregate order.filled (FlashOrderFilled) — NOT the QuickTrade
        # targetAssetAmount/contraAssetAmount/filledQty names. Reading the wrong
        # keys returns 0 on a genuinely FILLED order (silent-fill failure).
        fills = raw.get("fills") or []
        total_target = sum(
            safe_float(f.get("targetAmount"), 0)
            for f in fills
            if isinstance(f, dict)
        )
        total_contra = sum(
            safe_float(f.get("contraAmount"), 0)
            for f in fills
            if isinstance(f, dict)
        )
        order = raw.get("order", {})
        order_filled = (
            order.get("filled", {})
            if isinstance(order, dict)
            else {}
        )

        if isinstance(order_filled, dict):
            if total_target == 0:
                total_target = safe_float(
                    order_filled.get("targetAmount"),
                    0
                )
            if total_contra == 0:
                total_contra = safe_float(
                    order_filled.get("contraAmount"),
                    0
                )

        return {
            "target": total_target,
            "contra": total_contra
        }

    async def flash_wait_for_terminal_order(
        self,
        order_id,
        timeout_seconds
    ):

        deadline = time.time() + max(
            safe_float(timeout_seconds, 0),
            0
        )
        terminal_statuses = {
            "FILLED",
            "CANCELLED",
            "REJECTED",
            "TERMINATED"
        }
        last_detail = None

        while time.time() <= deadline:
            detail = await self.flash.get_order(order_id)

            if detail.get("ok"):
                last_detail = detail
                status = self.flash_normalized_order_status(detail)

                if status in terminal_statuses:
                    return detail

            await asyncio.sleep(2)

        return last_detail

    def flash_submit_result(
        self,
        *,
        event,
        side,
        qty,
        body,
        order_id="",
        filled_target=0,
        filled_contra=0,
        quote=None,
        submit=None,
        skipped=False,
        reason=""
    ):

        return {
            "enabled": self.flash_ordering_enabled(),
            "provider": "flash",
            "event_type": event.get("type", ""),
            "chain": body.get("targetChain", ""),
            "side": side,
            "qty": decimal_string(qty),
            "order_qty": body.get("qty", ""),
            "target_asset": body.get("targetAsset", ""),
            "contra_asset": body.get("contraAsset", ""),
            "dry_run": not self.flash_live_submit_enabled(),
            "accepted": bool(order_id),
            "submitted": bool(
                submit
                and submit.get("ok")
            ),
            "skipped": bool(skipped),
            "reason": reason,
            "order_id": order_id,
            "filled_target_amount": filled_target,
            "filled_contra_amount": filled_contra,
            "quote_ok": bool(
                quote
                and quote.get("ok")
            ),
            "quote_error": (
                quote.get("error", "")
                if isinstance(quote, dict)
                else ""
            ),
            "submit_status": (
                submit.get("status")
                if isinstance(submit, dict)
                else None
            ),
            "submit_error": (
                submit.get("error", "")
                if isinstance(submit, dict)
                else ""
            )
        }

    def flash_onchain_stop_armed(
        self
    ):

        # The on-chain catastrophe stop is ADDITIVE to the bot-managed exits and
        # must never act unless live submit is fully armed AND it is explicitly
        # enabled AND Flash credentials are present. Dormant otherwise.
        return (
            DEFINITIVE_FLASH_ONCHAIN_STOP_ENABLED
            and self.flash_live_submit_enabled()
            and self.flash.configured()
        )

    def flash_stop_quote_body(
        self,
        event,
        *,
        qty,
        trigger_usd
    ):

        chain = str(
            event.get("chain", "solana")
            or "solana"
        ).lower()

        return {
            "targetChain": chain,
            "contraChain": chain,
            "targetAsset": event.get("address"),
            "contraAsset": DEFINITIVE_SOLANA_CONTRA_ASSET,
            "side": "sell",
            "qty": decimal_string(qty),
            "orderType": DEFINITIVE_FLASH_ONCHAIN_STOP_ORDER_TYPE,
            # Flash wants a `triggers` ARRAY of PriceTrigger objects, not a
            # singular `priceTrigger` (the live OpenAPI has no such field).
            # triggerType enum is "upper"/"lower"; "lower" fires when price drops
            # to/below the trigger — the protective sell.
            "triggers": [
                {
                    "triggerType": "lower",
                    "notionalPrice": decimal_string(trigger_usd)
                }
            ],
            "maxSlippage": decimal_string(DEFINITIVE_FLASH_MAX_SLIPPAGE),
            "maxPriceImpact": decimal_string(
                DEFINITIVE_FLASH_MAX_PRICE_IMPACT
            ),
            "funderAddress": DEFINITIVE_FLASH_FUNDER_ADDRESS
        }

    async def place_flash_onchain_stop(
        self,
        event,
        *,
        qty,
        trigger_usd
    ):

        # Resting protective sell: quote -> sign -> submit, using the same SVM
        # signing as a market order. A stop order rests on Definitive's books
        # until its trigger is hit, so we do NOT wait for a terminal fill here.
        if not self.flash_onchain_stop_armed():
            return {
                "ok": False,
                "skipped": True,
                "reason": "onchain_stop_not_armed"
            }

        if qty <= 0 or trigger_usd <= 0:
            return {
                "ok": False,
                "skipped": True,
                "reason": "onchain_stop_invalid_params"
            }

        body = self.flash_stop_quote_body(
            event,
            qty=qty,
            trigger_usd=trigger_usd
        )
        quote = await self.flash.quote(body)

        if not quote.get("ok"):
            return {
                "ok": False,
                "skipped": True,
                "reason": quote.get("error", "onchain_stop_quote_failed")
            }

        raw_quote = quote.get("raw_response", {}) or {}
        quote_id = raw_quote.get("quoteId", "")
        svm = (
            raw_quote.get("svm", {})
            if isinstance(raw_quote.get("svm"), dict)
            else {}
        )
        order_message = svm.get("orderMessage", "")

        if not quote_id or not order_message:
            return {
                "ok": False,
                "skipped": True,
                "reason": "onchain_stop_quote_missing_svm_payload"
            }

        sponsored_delegate_tx = svm.get("sponsoredDelegateTx")

        try:
            signature = self.flash.sign_svm_order_message(order_message)
            signed_delegate_tx = (
                self.flash.sign_svm_sponsored_delegate_tx(
                    sponsored_delegate_tx
                )
                if sponsored_delegate_tx
                else None
            )
        except Exception as exc:
            return {
                "ok": False,
                "skipped": True,
                "reason": f"onchain_stop_signing_error:{exc}"
            }

        submit_body = dict(
            body,
            quoteId=quote_id,
            userSignature=signature,
            svmNonce=svm.get("nonce", ""),
            svmDeadline=svm.get("deadline", "")
        )
        if signed_delegate_tx:
            submit_body["svmSponsoredDelegateTx"] = signed_delegate_tx

        submit = await self.flash.submit_order(submit_body)

        if not submit.get("ok"):
            return {
                "ok": False,
                "skipped": True,
                "reason": submit.get("error", "onchain_stop_submit_failed")
            }

        order_id = self.flash_order_id_from_response(submit)

        return {
            "ok": bool(order_id),
            "order_id": order_id,
            "trigger_usd": trigger_usd,
            "qty": qty,
            "reason": "" if order_id else "onchain_stop_no_order_id"
        }

    async def cancel_flash_onchain_stop(
        self,
        order_id
    ):

        if not order_id:
            return {
                "ok": True,
                "reason": "no_onchain_stop_to_cancel"
            }

        if not self.flash.configured():
            return {
                "ok": False,
                "reason": "flash_credentials_missing"
            }

        result = await self.flash.cancel_order(order_id)

        return {
            "ok": bool(result.get("ok")),
            "order_id": order_id,
            "reason": (
                result.get("error", "")
                if not result.get("ok")
                else ""
            )
        }

    async def reconcile_flash_onchain_stop(
        self,
        event,
        *,
        existing_order_id,
        qty,
        trigger_usd
    ):

        # Flash has no modify endpoint: cancel the resting stop, then place a
        # fresh one. Used to update qty after a scale-out or ratchet the trigger.
        await self.cancel_flash_onchain_stop(existing_order_id)
        return await self.place_flash_onchain_stop(
            event,
            qty=qty,
            trigger_usd=trigger_usd
        )

    async def flash_list_open_stop_orders(
        self
    ):

        # Used to find orphaned on-chain stops (positions already closed) so they
        # can be cancelled during reconciliation.
        if not self.flash.configured():
            return []

        result = await self.flash.list_orders(
            status="ORDER_STATUS_PENDING"
        )
        raw = (
            result.get("raw_response", {})
            if isinstance(result, dict)
            else {}
        )

        if isinstance(raw, dict):
            orders = raw.get("orders", [])
        elif isinstance(raw, list):
            orders = raw
        else:
            orders = []

        open_stops = []
        for order in (orders or []):
            if not isinstance(order, dict):
                continue
            order_type = str(order.get("orderType", "") or "").lower()
            if "stop" in order_type:
                open_stops.append(order)

        return open_stops

    async def flash_list_open_take_profits(
        self
    ):

        # Resting take-profit orders rest in ORDER_STATUS_ACCEPTED (verified
        # live 2026-06-04). Used to suppress the bot's market scale-out while the
        # on-chain take-profit ladder is live.
        if not self.flash.configured():
            return []

        result = await self.flash.list_orders(
            status="ORDER_STATUS_ACCEPTED"
        )
        raw = (
            result.get("raw_response", {})
            if isinstance(result, dict)
            else {}
        )

        if isinstance(raw, dict):
            orders = raw.get("orders", [])
        elif isinstance(raw, list):
            orders = raw
        else:
            orders = []

        open_tps = []
        for order in (orders or []):
            if not isinstance(order, dict):
                continue
            order_type = str(order.get("orderType", "") or "").lower()
            if "take-profit" in order_type:
                open_tps.append(order)

        return open_tps

    async def manage_flash_onchain_stop(
        self,
        position_engine,
        event,
        result
    ):

        # Phase-2 hybrid backstop lifecycle. ADDITIVE to the bot-managed exits
        # (which stay primary) and fully gated + exception-safe, so it can never
        # disrupt the main execution flow. After a Flash entry fills it places a
        # wide on-chain stop, ratchets it up coarsely with the peak via
        # cancel-and-replace, resizes it after scale-outs, and cancels it on
        # close. Dormant unless flash_onchain_stop_armed().
        try:
            if not self.flash_onchain_stop_armed():
                return

            if str(result.get("provider", "")) != "flash":
                return

            if not result.get("submitted"):
                return

            position = position_engine.live_execution_position_for_event(
                event
            )
            if not position:
                return

            event_type = event.get("type", "")
            existing_id = position.get(
                "live_execution_onchain_stop_order_id",
                ""
            )
            remaining_tokens = safe_float(
                position.get("live_execution_remaining_tokens_estimated"),
                0
            )

            # CLOSE, or position fully exited: cancel the resting backstop.
            if event_type == "close" or remaining_tokens <= 0:
                if existing_id:
                    await self.cancel_flash_onchain_stop(existing_id)
                    position["live_execution_onchain_stop_order_id"] = ""
                    position["live_execution_onchain_stop_trigger_usd"] = 0
                    position["live_execution_onchain_stop_qty"] = 0
                    position_engine.save_state()
                return

            fill_price_usd = safe_float(
                position.get("live_execution_entry_fill_price_usd"),
                0
            )
            entry_price = safe_float(position.get("entry_price"), 0)

            if fill_price_usd <= 0 or entry_price <= 0:
                return

            stop_pct = position_engine.initial_stop_loss_pct(
                position=position
            )
            peak_price = safe_float(
                position.get("peak_price"),
                entry_price
            )

            # Coarse catastrophe trail: hold the backstop a route-initial-stop
            # distance below the PEAK, expressed against the USD fill price
            # (peak/entry is a unit-free ratio). Never below the entry floor.
            peak_ratio = max(
                peak_price / max(entry_price, 1e-18),
                1.0
            )
            entry_floor_usd = fill_price_usd * (1 - stop_pct)
            desired_trigger_usd = max(
                fill_price_usd * peak_ratio * (1 - stop_pct),
                entry_floor_usd
            )

            # ENTRY (no stop yet): place the initial wide backstop.
            if not existing_id:
                placed = await self.place_flash_onchain_stop(
                    event,
                    qty=remaining_tokens,
                    trigger_usd=entry_floor_usd
                )
                if placed.get("ok"):
                    position[
                        "live_execution_onchain_stop_order_id"
                    ] = placed.get("order_id", "")
                    position[
                        "live_execution_onchain_stop_trigger_usd"
                    ] = entry_floor_usd
                    position[
                        "live_execution_onchain_stop_qty"
                    ] = remaining_tokens
                    position_engine.save_state()
                    self._audit_resting_exit({
                        "event": "stop_placed",
                        "symbol": event.get("symbol", ""),
                        "mint": (event.get("mint") or event.get("token")
                                 or event.get("token_address") or ""),
                        "entry_fill_price_usd": fill_price_usd,
                        "stop_trigger_usd": entry_floor_usd,
                        "qty": remaining_tokens,
                        "order_id": placed.get("order_id", ""),
                    })
                else:
                    print(
                        "FLASH ONCHAIN STOP place skipped "
                        f"{event.get('symbol', '')} "
                        f"reason={placed.get('reason', '')}"
                    )
                return

            # Existing stop: cancel-and-replace if size changed materially
            # (scale-out) or the trigger ratcheted up past the configured margin.
            current_trigger_usd = safe_float(
                position.get("live_execution_onchain_stop_trigger_usd"),
                0
            )
            current_qty = safe_float(
                position.get("live_execution_onchain_stop_qty"),
                0
            )
            ratchet_margin = max(
                safe_float(DEFINITIVE_FLASH_ONCHAIN_STOP_RATCHET_MIN_PCT, 0),
                0
            )
            qty_changed = (
                remaining_tokens > 0
                and abs(remaining_tokens - current_qty)
                > current_qty * 0.01
            )
            ratcheted_up = (
                current_trigger_usd > 0
                and desired_trigger_usd
                >= current_trigger_usd * (1 + ratchet_margin)
            )

            if not (qty_changed or ratcheted_up):
                return

            new_trigger = max(desired_trigger_usd, current_trigger_usd)
            replaced = await self.reconcile_flash_onchain_stop(
                event,
                existing_order_id=existing_id,
                qty=remaining_tokens,
                trigger_usd=new_trigger
            )
            if replaced.get("ok"):
                position[
                    "live_execution_onchain_stop_order_id"
                ] = replaced.get("order_id", "")
                position[
                    "live_execution_onchain_stop_trigger_usd"
                ] = new_trigger
                position[
                    "live_execution_onchain_stop_qty"
                ] = remaining_tokens
            else:
                # The old stop was already cancelled inside reconcile; drop the
                # dead id so the next event re-places rather than tracking it.
                position["live_execution_onchain_stop_order_id"] = ""
                print(
                    "FLASH ONCHAIN STOP replace failed "
                    f"{event.get('symbol', '')} "
                    f"reason={replaced.get('reason', '')}"
                )
            position_engine.save_state()
        except Exception as exc:
            print(
                "FLASH ONCHAIN STOP error "
                f"{event.get('symbol', '')}: {exc}"
            )

    async def flash_ensure_svm_onboarded(
        self,
        quote_body,
        *,
        side,
        qty
    ):

        # Shared one-time SVM onboarding for any Flash order (buy or resting
        # sell). Quotes once to detect a non-sponsored delegateIx; if present,
        # broadcasts+confirms the create-ATAs (+ wrap for buys) + approve setup
        # tx. No-op when not armed, already delegated, or sponsorship offered.
        if not self.flash_live_submit_enabled():
            return {"ok": True, "skipped": True, "reason": "not_armed"}

        preflight = await self.flash.quote(quote_body)
        pre_svm = {}
        if preflight.get("ok"):
            pre_svm = (
                preflight.get("raw_response", {}) or {}
            ).get("svm", {}) or {}

        sponsored = bool(pre_svm.get("sponsoredDelegateTx"))
        has_delegate_ix = bool(pre_svm.get("delegateIx")) and not sponsored

        # Per-trade wSOL funding: a buy SPENDS wrapped SOL, so before every buy
        # top the wSOL ATA up to cover this trade (wrap only the deficit). The
        # delegate grant is one-time; the wrap is NOT. Sells need no wrap.
        # Definitive may sponsor the delegate setup, but live Flash submits still
        # require the source account to hold pre-wrapped SOL.
        needed_lamports = 0
        wrap_lamports = 0
        if side == "buy":
            needed_lamports = (
                int(safe_float(qty, 0) * 1e9)
                + self.flash.SVM_WRAP_BUFFER_LAMPORTS
            )
            current = await self.flash.wsol_balance_lamports()
            wrap_lamports = max(0, needed_lamports - current)

        # Nothing to broadcast if already delegated and the wSOL ATA is funded.
        if not has_delegate_ix and wrap_lamports <= 0:
            return {"ok": True, "skipped": True, "reason": "no_onboarding"}

        onboard = await self.flash.ensure_svm_onboarding(
            pre_svm,
            target_mint=quote_body.get("targetAsset"),
            contra_mint=DEFINITIVE_SOLANA_CONTRA_ASSET,
            wrap_lamports=wrap_lamports
        )
        if (
            onboard.get("ok")
            and side == "buy"
            and wrap_lamports > 0
            and DEFINITIVE_FLASH_WRAP_SETTLE_SECONDS > 0
        ):
            await asyncio.sleep(DEFINITIVE_FLASH_WRAP_SETTLE_SECONDS)
            current = await self.flash.wsol_balance_lamports()
            if current < needed_lamports:
                return {
                    "ok": False,
                    "error": (
                        "wsol_wrap_balance_short:"
                        f"have={current}:need={needed_lamports}"
                    ),
                    "raw_response": onboard
                }
        return onboard

    def flash_resting_exits_armed(
        self
    ):

        # Resting take-profit ladder, ADDITIVE and triple-gated exactly like the
        # onchain stop. Pair it WITH the onchain stop (enable both: this owns the
        # take-profits, that owns the protective stop). Dormant otherwise.
        return (
            DEFINITIVE_FLASH_RESTING_EXITS_ENABLED
            and self.flash_live_submit_enabled()
            and self.flash.configured()
        )

    def flash_trigger_quote_body(
        self,
        event,
        *,
        qty,
        order_type,
        triggers
    ):

        chain = str(event.get("chain", "solana") or "solana").lower()
        return {
            "targetChain": chain,
            "contraChain": chain,
            "targetAsset": event.get("address"),
            "contraAsset": DEFINITIVE_SOLANA_CONTRA_ASSET,
            "side": "sell",
            "qty": decimal_string(qty),
            "orderType": order_type,
            "triggers": triggers,
            "maxSlippage": decimal_string(DEFINITIVE_FLASH_MAX_SLIPPAGE),
            "maxPriceImpact": decimal_string(
                DEFINITIVE_FLASH_MAX_PRICE_IMPACT
            ),
            "funderAddress": DEFINITIVE_FLASH_FUNDER_ADDRESS
        }

    async def place_flash_trigger_order(
        self,
        event,
        *,
        qty,
        order_type,
        triggers
    ):

        # Resting sell-side exit (stop-loss / take-profit / bracket):
        # quote -> onboard sell side -> sign -> submit. Rests on Definitive's
        # books; does NOT wait for a terminal fill.
        if not self.flash_live_submit_enabled():
            return {
                "ok": False,
                "skipped": True,
                "reason": "live_submit_not_armed"
            }

        if safe_float(qty, 0) <= 0:
            return {
                "ok": False,
                "skipped": True,
                "reason": "trigger_invalid_qty"
            }

        body = self.flash_trigger_quote_body(
            event,
            qty=qty,
            order_type=order_type,
            triggers=triggers
        )

        onboard = await self.flash_ensure_svm_onboarded(
            body,
            side="sell",
            qty=qty
        )
        if not onboard.get("ok"):
            return {
                "ok": False,
                "skipped": True,
                "reason": f"trigger_onboarding_failed:{onboard.get('error')}"
            }

        quote = await self.flash.quote(body)
        if not quote.get("ok"):
            return {
                "ok": False,
                "skipped": True,
                "reason": quote.get("error", "trigger_quote_failed")
            }

        raw_quote = quote.get("raw_response", {}) or {}
        quote_id = raw_quote.get("quoteId", "")
        svm = (
            raw_quote.get("svm", {})
            if isinstance(raw_quote.get("svm"), dict)
            else {}
        )
        order_message = svm.get("orderMessage", "")

        if not quote_id or not order_message:
            return {
                "ok": False,
                "skipped": True,
                "reason": "trigger_quote_missing_svm_payload"
            }

        try:
            signature = self.flash.sign_svm_order_message(order_message)
            sponsored = svm.get("sponsoredDelegateTx")
            signed_delegate_tx = (
                self.flash.sign_svm_sponsored_delegate_tx(sponsored)
                if sponsored
                else None
            )
        except Exception as exc:
            return {
                "ok": False,
                "skipped": True,
                "reason": f"trigger_signing_error:{exc}"
            }

        submit_body = dict(
            body,
            quoteId=quote_id,
            userSignature=signature,
            svmNonce=svm.get("nonce", ""),
            svmDeadline=svm.get("deadline", "")
        )
        if signed_delegate_tx:
            submit_body["svmSponsoredDelegateTx"] = signed_delegate_tx

        submit = await self.flash.submit_order(submit_body)
        if not submit.get("ok"):
            return {
                "ok": False,
                "skipped": True,
                "reason": submit.get("error", "trigger_submit_failed")
            }

        order_id = self.flash_order_id_from_response(submit)
        return {
            "ok": bool(order_id),
            "order_id": order_id,
            "order_type": order_type,
            "qty": qty
        }

    def _audit_resting_exit(self, record):
        # B3 instrumentation: append a structured line capturing resting-exit
        # INTENT (TP ladder + stop trigger at placement) and FILL detection
        # (token delta at reconcile), so LIVE resting-stop/TP fill quality
        # (slip vs intended trigger) can be measured once live arms. Exact fill
        # prices are recoverable post-hoc from the logged order_ids via
        # flash_order_filled_amounts(order status). Logging-only, exception-safe,
        # no behaviour change; only ever called from already-armed code paths.
        try:
            import os
            record = dict(record)
            record.setdefault("ts", time.time())
            path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "logs",
                "resting_exit_audit.jsonl",
            )
            with open(path, "a") as fh:
                fh.write(json.dumps(record) + "\n")
        except Exception:
            pass

    async def manage_flash_resting_exits(
        self,
        position_engine,
        event,
        result
    ):

        # Places a resting take-profit LADDER on-chain after a Flash entry fills
        # (one take-profit / upper-trigger order per scale-out rung) so profits
        # fire server-side between scans, and cancels them on close. The
        # protective stop is owned by manage_flash_onchain_stop -- enable BOTH
        # flags. ADDITIVE, gated, exception-safe. (Flash brackets are not yet
        # supported, so take-profit and stop are separate resting orders.)
        # Dormant unless flash_resting_exits_armed().
        try:
            if not self.flash_resting_exits_armed():
                return

            if str(result.get("provider", "")) != "flash":
                return

            if not result.get("submitted"):
                return

            position = position_engine.live_execution_position_for_event(
                event
            )
            if not position:
                return

            event_type = event.get("type", "")
            existing = position.get(
                "live_execution_flash_bracket_orders"
            ) or []
            remaining_tokens = safe_float(
                position.get("live_execution_remaining_tokens_estimated"),
                0
            )

            # CLOSE, or position fully exited: cancel all resting exits.
            if event_type == "close" or remaining_tokens <= 0:
                if existing:
                    for order in existing:
                        order_id = (
                            order.get("order_id")
                            if isinstance(order, dict)
                            else None
                        )
                        if order_id:
                            await self.cancel_flash_onchain_stop(order_id)
                    position["live_execution_flash_bracket_orders"] = []
                    position_engine.save_state()
                return

            # Place the ladder once, on the entry that opened the position.
            if existing or event_type != "entry":
                return

            fill_price_usd = safe_float(
                position.get("live_execution_entry_fill_price_usd"),
                0
            )
            if fill_price_usd <= 0 or remaining_tokens <= 0:
                return

            ladder = position_engine.scale_out_ladder(position) or []

            # Flash brackets are not yet supported by the API, so the resting
            # take-profit LADDER is placed as plain take-profit (upper-trigger)
            # orders, one per scale-out rung. The protective stop is owned by
            # manage_flash_onchain_stop (enable BOTH flags): it places, ratchets
            # and RESIZES a full-position stop, which keeps the stop qty correct
            # as these take-profits fill. The runner slice rides with that stop.
            placed = []
            prev_cum = 0.0
            for rung in ladder:
                try:
                    multiple = safe_float(rung[0], 0)
                    cum_pct = safe_float(rung[1], 0)
                except Exception:
                    continue
                slice_pct = max(cum_pct - prev_cum, 0)
                prev_cum = cum_pct
                if multiple <= 0 or slice_pct <= 0:
                    continue
                slice_qty = remaining_tokens * slice_pct
                tp_usd = fill_price_usd * multiple
                take_profit = await self.place_flash_trigger_order(
                    event,
                    qty=slice_qty,
                    order_type="take-profit",
                    triggers=[
                        {
                            "triggerType": "upper",
                            "notionalPrice": decimal_string(tp_usd)
                        }
                    ]
                )
                if take_profit.get("ok"):
                    placed.append({
                        "order_id": take_profit.get("order_id"),
                        "kind": "take_profit",
                        "tp_usd": tp_usd,
                        "qty": slice_qty
                    })
                else:
                    print(
                        "FLASH RESTING take-profit skipped "
                        f"{event.get('symbol', '')} mult={multiple} "
                        f"reason={take_profit.get('reason', '')}"
                    )

            if placed:
                position["live_execution_flash_bracket_orders"] = placed
                position_engine.save_state()
                self._audit_resting_exit({
                    "event": "tp_ladder_placed",
                    "symbol": event.get("symbol", ""),
                    "mint": (event.get("mint") or event.get("token")
                             or event.get("token_address") or ""),
                    "entry_fill_price_usd": fill_price_usd,
                    "orders": [
                        {"order_id": o.get("order_id"), "kind": o.get("kind"),
                         "trigger_usd": o.get("tp_usd"), "qty": o.get("qty")}
                        for o in placed
                    ],
                })
                print(
                    "FLASH RESTING take-profits placed "
                    f"{event.get('symbol', '')} count={len(placed)}"
                )
        except Exception as exc:
            print(
                "FLASH RESTING exits error "
                f"{event.get('symbol', '')}: {exc}"
            )

    async def reconcile_flash_resting_exits(
        self,
        position_engine,
        position,
        mint
    ):

        # On-chain fills (resting take-profits and the stop) reduce the wallet
        # token balance independently of the bot. Sync the live token estimate to
        # the actual on-chain balance (ground truth) so the protective stop is
        # sized correctly and exposure stays accurate. Gated + exception-safe;
        # dormant unless armed AND resting exits are placed for this position.
        try:
            if not self.flash_resting_exits_armed():
                return
            if not position or not mint:
                return
            if not position.get("live_execution_flash_bracket_orders"):
                return

            target_program = await self.flash.solana_token_program(mint)
            owner = self.flash.solana_keypair().pubkey()
            ata = self.flash.derive_associated_token_account(
                owner,
                mint,
                target_program
            )
            balance = await self.flash.solana_rpc(
                "getTokenAccountBalance",
                [str(ata), {"commitment": "confirmed"}]
            )
            ui_amount = (
                (balance.get("result", {}) or {}).get("value", {}) or {}
            ).get("uiAmount")
            if ui_amount is None:
                return

            on_chain_tokens = safe_float(ui_amount, 0)
            prev = safe_float(
                position.get("live_execution_remaining_tokens_estimated"),
                0
            )
            # Act only on a meaningful decrease (a resting order filled).
            if prev > 0 and on_chain_tokens < prev * 0.99:
                position[
                    "live_execution_remaining_tokens_estimated"
                ] = on_chain_tokens
                position_engine.save_state()
                bracket = position.get(
                    "live_execution_flash_bracket_orders"
                ) or []
                self._audit_resting_exit({
                    "event": "resting_fill_detected",
                    "symbol": position.get("symbol", ""),
                    "mint": mint,
                    "prev_tokens": prev,
                    "on_chain_tokens": on_chain_tokens,
                    "tokens_sold": prev - on_chain_tokens,
                    "entry_fill_price_usd": position.get(
                        "live_execution_entry_fill_price_usd"),
                    "tp_orders": [
                        {"order_id": o.get("order_id"),
                         "trigger_usd": o.get("tp_usd"), "qty": o.get("qty")}
                        for o in bracket if isinstance(o, dict)
                    ],
                    "stop_order_id": position.get(
                        "live_execution_onchain_stop_order_id", ""),
                    "stop_trigger_usd": position.get(
                        "live_execution_onchain_stop_trigger_usd", 0),
                })
                print(
                    "FLASH RESTING reconcile "
                    f"{position.get('symbol', '')} "
                    f"tokens {prev:.0f} -> {on_chain_tokens:.0f}"
                )
        except Exception as exc:
            print(
                "FLASH RESTING reconcile error "
                f"{position.get('symbol', '')}: {exc}"
            )

    async def execute_flash_position_event(
        self,
        event,
        *,
        open_summary=None,
        has_live_position=False
    ):

        event = event or {}
        event_type = event.get("type", "")

        if event_type not in (
            "entry",
            "scale_out",
            "live_scale_out",
            "close"
        ):
            return {
                "enabled": self.flash_ordering_enabled(),
                "provider": "flash",
                "skipped": True,
                "reason": "unsupported_event_type",
                "event_type": event_type
            }

        if not self.flash_ordering_enabled():
            return {
                "enabled": False,
                "provider": "flash",
                "skipped": True,
                "reason": "flash_execution_disabled",
                "event_type": event_type
            }

        if not self.flash.configured():
            return {
                "enabled": True,
                "provider": "flash",
                "skipped": True,
                "reason": "flash_credentials_missing",
                "event_type": event_type
            }

        chain = str(
            event.get("chain", "solana")
            or "solana"
        ).lower()

        if chain != "solana":
            return {
                "enabled": True,
                "provider": "flash",
                "skipped": True,
                "reason": f"flash_chain_not_supported:{chain}",
                "event_type": event_type
            }

        # Resting take-profit ladder owns the scale-out: when armed and a resting
        # take-profit is live for this funder, suppress the bot's market
        # scale-out so the two never both sell. The on-chain order fills
        # server-side between scans; reconcile_flash_resting_exits then syncs the
        # live token estimate. Full closes and stops still execute normally.
        if (
            event_type in ("scale_out", "live_scale_out")
            and self.flash_resting_exits_armed()
        ):
            if await self.flash_list_open_take_profits():
                return {
                    "enabled": True,
                    "provider": "flash",
                    "skipped": True,
                    "reason": "suppressed_by_resting_take_profits",
                    "event_type": event_type
                }

        if event_type == "entry":
            summary = open_summary or {}
            qty_usd = self.definitive_entry_notional_usd(
                event,
                open_summary=summary
            )

            if qty_usd < DEFINITIVE_MIN_ENTRY_NOTIONAL_USD:
                return {
                    "enabled": True,
                    "provider": "flash",
                    "skipped": True,
                    "reason": "entry_notional_below_min_or_exposure_full",
                    "event_type": event_type,
                    "qty": decimal_string(qty_usd)
                }

            contra_price_usd = self.event_contra_asset_usd_price(event)

            if contra_price_usd <= 0:
                return {
                    "enabled": True,
                    "provider": "flash",
                    "skipped": True,
                    "reason": "contra_asset_usd_price_missing_for_contra_qty",
                    "event_type": event_type
                }

            qty = qty_usd / contra_price_usd
            side = "buy"

        else:
            if not has_live_position:
                return {
                    "enabled": True,
                    "provider": "flash",
                    "skipped": True,
                    "reason": "no_live_entry_for_position",
                    "event_type": event_type
                }

            price = safe_float(event.get("last_price"), 0)
            qty = safe_float(
                event.get("live_execution_sell_tokens"),
                0
            )

            if qty <= 0:
                qty = (
                    safe_float(event.get("proceeds_usd"), 0)
                    / max(price, 1e-18)
                )

            remaining_tokens = safe_float(
                event.get("live_execution_remaining_tokens_estimated"),
                0
            )

            if remaining_tokens > 0:
                qty = min(qty, remaining_tokens)

            if qty <= 0:
                return {
                    "enabled": True,
                    "provider": "flash",
                    "skipped": True,
                    "reason": "zero_exit_quantity",
                    "event_type": event_type
                }

            side = "sell"

        quote_body = {
            "targetChain": chain,
            "contraChain": chain,
            "targetAsset": event.get("address"),
            "contraAsset": DEFINITIVE_SOLANA_CONTRA_ASSET,
            "side": side,
            "qty": decimal_string(qty),
            "orderType": "market",
            "maxSlippage": decimal_string(DEFINITIVE_FLASH_MAX_SLIPPAGE),
            "maxPriceImpact": decimal_string(
                DEFINITIVE_FLASH_MAX_PRICE_IMPACT
            ),
            "funderAddress": DEFINITIVE_FLASH_FUNDER_ADDRESS
        }

        if (
            side == "buy"
            and str(DEFINITIVE_SOLANA_CONTRA_ASSET or "") == SOL_MINT
        ):
            needed_lamports = (
                int(safe_float(qty, 0) * 1e9)
                + self.flash.SVM_WRAP_BUFFER_LAMPORTS
            )
            current_wsol_lamports = await self.flash.wsol_balance_lamports()
            wrap_deficit_sol = (
                max(0, needed_lamports - current_wsol_lamports) / 1e9
            )
            balance_sol = await self.solana_sol_balance(
                DEFINITIVE_FLASH_FUNDER_ADDRESS
            )

            if (
                balance_sol is not None
                and wrap_deficit_sol > 0
                and balance_sol < wrap_deficit_sol
            ):
                return self.flash_submit_result(
                    event=event,
                    side=side,
                    qty=qty,
                    body=quote_body,
                    skipped=True,
                    reason=(
                        "flash_insufficient_funder_sol_balance:"
                        f"have={decimal_string(balance_sol)}:"
                        f"need_wrap={decimal_string(wrap_deficit_sol)}:"
                        f"wsol_have={current_wsol_lamports}"
                    )
                )

        max_attempts = max(
            safe_int(DEFINITIVE_FLASH_SUBMIT_MAX_ATTEMPTS, 1),
            1
        )
        retry_delay = max(
            safe_float(DEFINITIVE_FLASH_SUBMIT_RETRY_DELAY_SECONDS, 0),
            0
        )
        last_result = None

        # First non-sponsored trade for this wallet needs an on-chain delegate
        # grant (and, for buys, wrapped SOL) before the timed submit loop. No-op
        # when already delegated or when Definitive sponsors the setup.
        onboard = await self.flash_ensure_svm_onboarded(
            quote_body,
            side=side,
            qty=qty
        )
        if not onboard.get("ok"):
            return self.flash_submit_result(
                event=event,
                side=side,
                qty=qty,
                body=quote_body,
                skipped=True,
                reason=f"flash_onboarding_failed:{onboard.get('error')}"
            )

        for attempt in range(max_attempts):
            quote = await self.flash.quote(quote_body)

            if not quote.get("ok"):
                last_result = self.flash_submit_result(
                    event=event,
                    side=side,
                    qty=qty,
                    body=quote_body,
                    quote=quote,
                    skipped=True,
                    reason=quote.get("error", "flash_quote_failed")
                )

                if attempt + 1 < max_attempts and retry_delay > 0:
                    await asyncio.sleep(retry_delay)

                continue

            raw_quote = quote.get("raw_response", {}) or {}
            quote_id = raw_quote.get("quoteId", "")
            svm = raw_quote.get("svm", {})
            order_message = (
                svm.get("orderMessage", "")
                if isinstance(svm, dict)
                else ""
            )

            if not quote_id or not order_message:
                last_result = self.flash_submit_result(
                    event=event,
                    side=side,
                    qty=qty,
                    body=quote_body,
                    quote=quote,
                    skipped=True,
                    reason="flash_quote_missing_svm_payload"
                )
                break

            if not self.flash_live_submit_enabled():
                return self.flash_submit_result(
                    event=event,
                    side=side,
                    qty=qty,
                    body=quote_body,
                    quote=quote,
                    skipped=True,
                    reason="live_submit_not_armed"
                )

            svm_nonce = (
                svm.get("nonce", "")
                if isinstance(svm, dict)
                else ""
            )
            svm_deadline = (
                svm.get("deadline", "")
                if isinstance(svm, dict)
                else ""
            )
            sponsored_delegate_tx = (
                svm.get("sponsoredDelegateTx")
                if isinstance(svm, dict)
                else None
            )

            try:
                signature = self.flash.sign_svm_order_message(order_message)
                # First trade for a token returns a gasless delegate tx; sign
                # it so Definitive can pull funds at execution. Null afterward.
                signed_delegate_tx = (
                    self.flash.sign_svm_sponsored_delegate_tx(
                        sponsored_delegate_tx
                    )
                    if sponsored_delegate_tx
                    else None
                )
            except Exception as exc:
                last_result = self.flash_submit_result(
                    event=event,
                    side=side,
                    qty=qty,
                    body=quote_body,
                    quote=quote,
                    skipped=True,
                    reason=f"flash_signing_error:{exc}"
                )
                break

            submit_body = dict(
                quote_body,
                quoteId=quote_id,
                userSignature=signature,
                svmNonce=svm_nonce,
                svmDeadline=svm_deadline
            )
            if signed_delegate_tx:
                submit_body["svmSponsoredDelegateTx"] = signed_delegate_tx

            submit = await self.flash.submit_order(submit_body)

            if submit.get("ok"):
                order_id = self.flash_order_id_from_response(submit)

                if order_id:
                    detail = await self.flash_wait_for_terminal_order(
                        order_id,
                        DEFINITIVE_FLASH_CONFIRM_FILL_SECONDS
                    )
                    status = (
                        self.flash_normalized_order_status(detail)
                        if detail
                        else ""
                    )
                    filled = (
                        self.flash_order_filled_amounts(detail)
                        if detail
                        else {}
                    )

                    if status == "FILLED":
                        return self.flash_submit_result(
                            event=event,
                            side=side,
                            qty=qty,
                            body=quote_body,
                            order_id=order_id,
                            filled_target=filled.get("target", 0),
                            filled_contra=filled.get("contra", 0),
                            quote=quote,
                            submit=submit
                        )

                    if status in (
                        "CANCELLED",
                        "REJECTED",
                        "TERMINATED"
                    ):
                        close_reason = self.flash_order_close_reason(detail)
                        last_result = self.flash_submit_result(
                            event=event,
                            side=side,
                            qty=qty,
                            body=quote_body,
                            order_id=order_id,
                            quote=quote,
                            submit=submit,
                            skipped=True,
                            reason=(
                                f"flash_order_{status.lower()}"
                                + (f":{close_reason}" if close_reason else "")
                            )
                        )
                    else:
                        cancel = await self.flash.cancel_order(order_id)
                        cancelled_detail = (
                            await self.flash.get_order(order_id)
                            if cancel.get("ok")
                            else None
                        )
                        cancelled_status = (
                            self.flash_normalized_order_status(
                                cancelled_detail
                            )
                            if cancelled_detail
                            else ""
                        )
                        cancelled_filled = (
                            self.flash_order_filled_amounts(cancelled_detail)
                            if cancelled_detail
                            else {}
                        )

                        if cancelled_filled.get("target", 0) > 0:
                            return self.flash_submit_result(
                                event=event,
                                side=side,
                                qty=qty,
                                body=quote_body,
                                order_id=order_id,
                                filled_target=cancelled_filled.get(
                                    "target",
                                    0
                                ),
                                filled_contra=cancelled_filled.get(
                                    "contra",
                                    0
                                ),
                                quote=quote,
                                submit=submit,
                                reason="flash_filled_after_timeout"
                            )

                        if (
                            cancel.get("ok")
                            and cancelled_status in (
                                "CANCELLED",
                                "REJECTED",
                                "TERMINATED"
                            )
                        ):
                            cancelled_close_reason = (
                                self.flash_order_close_reason(
                                    cancelled_detail
                                )
                                if cancelled_detail
                                else ""
                            )
                            last_result = self.flash_submit_result(
                                event=event,
                                side=side,
                                qty=qty,
                                body=quote_body,
                                order_id=order_id,
                                quote=quote,
                                submit=submit,
                                skipped=True,
                                reason=(
                                    "flash_fill_timeout_cancelled"
                                    + (
                                        f":{cancelled_close_reason}"
                                        if cancelled_close_reason
                                        else ""
                                    )
                                )
                            )
                        else:
                            return self.flash_submit_result(
                                event=event,
                                side=side,
                                qty=qty,
                                body=quote_body,
                                order_id=order_id,
                                filled_target=filled.get("target", 0),
                                filled_contra=filled.get("contra", 0),
                                quote=quote,
                                submit=submit,
                                reason="flash_fill_timeout_uncertain_no_retry"
                            )

                        if attempt + 1 < max_attempts:
                            if retry_delay > 0:
                                await asyncio.sleep(retry_delay)
                            continue

                        return last_result or self.flash_submit_result(
                            event=event,
                            side=side,
                            qty=qty,
                            body=quote_body,
                            order_id=order_id,
                            filled_target=filled.get("target", 0),
                            filled_contra=filled.get("contra", 0),
                            quote=quote,
                            submit=submit,
                            reason="flash_fill_timeout"
                        )

                else:
                    return self.flash_submit_result(
                        event=event,
                        side=side,
                        qty=qty,
                        body=quote_body,
                        quote=quote,
                        submit=submit
                    )

            else:
                last_result = self.flash_submit_result(
                    event=event,
                    side=side,
                    qty=qty,
                    body=quote_body,
                    quote=quote,
                    submit=submit,
                    skipped=True,
                    reason=submit.get("error", "flash_submit_failed")
                )

            if attempt + 1 < max_attempts and retry_delay > 0:
                await asyncio.sleep(retry_delay)

        return last_result or {
            "enabled": True,
            "provider": "flash",
            "submitted": False,
            "skipped": True,
            "reason": "flash_submit_failed_without_result"
        }

    async def execute_position_event(
        self,
        event,
        *,
        open_summary=None,
        has_live_position=False
    ):

        chain = str(
            (event or {}).get("chain", "solana")
            or "solana"
        ).lower()

        if (
            self.gmgn_trading_enabled()
            and self.gmgn.configured()
            and chain == "solana"
        ):
            return await self.execute_gmgn_position_event(
                event,
                open_summary=open_summary,
                has_live_position=has_live_position
            )

        if (
            self.flash_ordering_enabled()
            and self.flash.configured()
            and chain == "solana"
        ):
            return await self.execute_flash_position_event(
                event,
                open_summary=open_summary,
                has_live_position=has_live_position
            )

        return await self.execute_definitive_position_event(
            event,
            open_summary=open_summary,
            has_live_position=has_live_position
        )

    async def execute_gmgn_position_event(
        self,
        event,
        *,
        open_summary=None,
        has_live_position=False
    ):

        event = event or {}
        event_type = event.get("type", "")
        token = event.get("address")

        if event_type not in (
            "entry",
            "scale_out",
            "live_scale_out",
            "close"
        ):
            return self.gmgn_submit_result(
                event=event, side="", qty=0, target_asset=token,
                skipped=True, reason="unsupported_event_type"
            )

        if str(event.get("chain", "solana") or "solana").lower() != "solana":
            return self.gmgn_submit_result(
                event=event, side="", qty=0, target_asset=token,
                skipped=True, reason="gmgn_chain_not_supported"
            )

        if event_type == "entry":
            qty_usd = self.definitive_entry_notional_usd(
                event,
                open_summary=open_summary or {}
            )

            if qty_usd < DEFINITIVE_MIN_ENTRY_NOTIONAL_USD:
                return self.gmgn_submit_result(
                    event=event, side="buy", qty=qty_usd, target_asset=token,
                    skipped=True,
                    reason="entry_notional_below_min_or_exposure_full"
                )

            sol_usd = self.event_contra_asset_usd_price(event)
            if sol_usd <= 0:
                return self.gmgn_submit_result(
                    event=event, side="buy", qty=qty_usd, target_asset=token,
                    skipped=True, reason="sol_usd_price_missing"
                )

            qty_sol = qty_usd / sol_usd
            balance_sol = await self.solana_sol_balance(GMGN_TRADING_WALLET)
            if balance_sol is not None and balance_sol < qty_sol:
                return self.gmgn_submit_result(
                    event=event, side="buy", qty=qty_sol, target_asset=token,
                    skipped=True,
                    reason=(
                        "gmgn_insufficient_sol:"
                        f"have={decimal_string(balance_sol)}:"
                        f"need={decimal_string(qty_sol)}"
                    )
                )

            if not self.gmgn_live_submit_enabled():
                return self.gmgn_submit_result(
                    event=event, side="buy", qty=qty_sol, target_asset=token,
                    skipped=True, reason="live_submit_not_armed"
                )

            balance_before = await self.gmgn.token_balance(token) or 0.0
            lamports = int(qty_sol * 1e9)
            condition_orders = self.gmgn.entry_condition_orders()
            condition_order_count = len(condition_orders or [])
            submit = await self.gmgn.swap_buy(
                token,
                lamports,
                condition_orders=condition_orders
            )

            if not submit.get("ok"):
                return self.gmgn_submit_result(
                    event=event, side="buy", qty=qty_sol, target_asset=token,
                    skipped=True,
                    reason=f"gmgn_swap_failed:{submit.get('error', '')}"
                )

            raw = submit.get("raw_response") or {}
            order_id = str(
                self.gmgn.response_value(
                    raw,
                    "hash",
                    "tx_hash",
                    "order_id",
                    "signature"
                )
                if isinstance(raw, dict)
                else ""
            )
            strategy_order_id = str(
                self.gmgn.response_value(
                    raw,
                    "strategy_order_id",
                    "strategyOrderId",
                    "strategy_id"
                )
                if isinstance(raw, dict)
                else ""
            )

            deadline = time.monotonic() + max(
                DEFINITIVE_FLASH_CONFIRM_FILL_SECONDS, 10
            )
            filled = 0.0
            while time.monotonic() < deadline:
                await asyncio.sleep(3)
                balance_now = await self.gmgn.token_balance(token)
                if balance_now is not None:
                    delta = balance_now - balance_before
                    if delta > 0:
                        filled = delta
                        break

            reason = "" if filled > 0 else "gmgn_fill_unconfirmed"
            if condition_order_count and not strategy_order_id:
                reason = (
                    f"{reason}:gmgn_strategy_unconfirmed"
                    if reason
                    else "gmgn_strategy_unconfirmed"
                )

            return self.gmgn_submit_result(
                event=event, side="buy", qty=qty_sol, target_asset=token,
                order_id=order_id,
                submitted=True,
                filled_target=filled,
                filled_contra=qty_sol if filled > 0 else 0,
                reason=reason,
                entry_notional_usd=qty_usd,
                strategy_order_id=strategy_order_id,
                condition_order_count=condition_order_count
            )

        # sells (scale_out / live_scale_out / close)
        if not has_live_position:
            return self.gmgn_submit_result(
                event=event, side="sell", qty=0, target_asset=token,
                skipped=True, reason="no_live_entry_for_position"
            )

        sell_tokens = safe_float(
            event.get("live_execution_sell_tokens"), 0
        )
        remaining = safe_float(
            event.get("live_execution_remaining_tokens_estimated"), 0
        )

        if event_type == "close" or (
            remaining > 0 and sell_tokens >= remaining * 0.99
        ):
            percent = 100
        elif remaining > 0 and sell_tokens > 0:
            percent = max(1, min(100, round(sell_tokens / remaining * 100)))
        else:
            return self.gmgn_submit_result(
                event=event, side="sell", qty=0, target_asset=token,
                skipped=True, reason="zero_exit_quantity"
            )

        if not self.gmgn_live_submit_enabled():
            return self.gmgn_submit_result(
                event=event, side="sell", qty=sell_tokens, target_asset=token,
                skipped=True, reason="live_submit_not_armed"
            )

        cleanup_note = ""
        if percent >= 100:
            try:
                cleanup = await self.gmgn.cancel_open_strategies_for_token(
                    token
                )
                if cleanup.get("ok"):
                    if cleanup.get("found"):
                        cleanup_note = (
                            f":pre_strategies_cancelled_"
                            f"{cleanup.get('cancelled', 0)}"
                            f"of{cleanup.get('found', 0)}"
                        )
                else:
                    cleanup_note = ":pre_strategy_cleanup_failed"
            except Exception as exc:
                cleanup_note = f":pre_strategy_cleanup_error_{type(exc).__name__}"

        submit = await self.gmgn.swap_sell_percent(token, percent)

        if not submit.get("ok"):
            error = submit.get("error", "")
            if (
                percent >= 100
                and self.gmgn.insufficient_balance_error(error)
            ):
                balance_now = await self.gmgn.token_balance(token)
                if balance_now is not None and balance_now <= 0:
                    return self.gmgn_submit_result(
                        event=event,
                        side="sell",
                        qty=0,
                        target_asset=token,
                        submitted=True,
                        filled_target=0,
                        filled_contra=0,
                        reason=(
                            "gmgn_sell_already_flat_after_insufficient_balance"
                            f"{cleanup_note}"
                        )
                    )

            return self.gmgn_submit_result(
                event=event, side="sell", qty=sell_tokens, target_asset=token,
                skipped=True,
                reason=f"gmgn_swap_failed:{error}"
            )

        raw = submit.get("raw_response") or {}
        order_id = str(
            raw.get("hash")
            or raw.get("tx_hash")
            or raw.get("order_id")
            or raw.get("signature")
            or (raw.get("data") or {}).get("hash", "")
            if isinstance(raw, dict)
            else ""
        )
        price = safe_float(event.get("last_price"), 0)
        sol_usd = self.event_contra_asset_usd_price(event)
        est_tokens = sell_tokens or (remaining * percent / 100.0)

        # full close -> cancel any resting TP/SL so a zombie strategy order
        # can't fire against a future re-entry of the same token
        if percent >= 100:
            try:
                cleanup = await self.gmgn.cancel_open_strategies_for_token(
                    token
                )
                if cleanup.get("found"):
                    cleanup_note = (
                        f"{cleanup_note}:strategies_cancelled_"
                        f"{cleanup.get('cancelled', 0)}"
                        f"of{cleanup.get('found', 0)}"
                    )
            except Exception as exc:
                cleanup_note = (
                    f"{cleanup_note}:strategy_cleanup_error_"
                    f"{type(exc).__name__}"
                )

        return self.gmgn_submit_result(
            event=event, side="sell", qty=est_tokens, target_asset=token,
            order_id=order_id,
            submitted=True,
            filled_target=est_tokens,
            filled_contra=(
                est_tokens * price / sol_usd if sol_usd > 0 else 0
            ),
            reason=f"gmgn_sell_{percent}pct{cleanup_note}"
        )
