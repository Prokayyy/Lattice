import asyncio
import time
from urllib.parse import urlparse

from config import (
    ALCHEMY_GRPC_ENDPOINT,
    ALCHEMY_GRPC_X_TOKEN,
    ENABLE_ALCHEMY_GRPC,
    GRPC_MAX_WATCH_ACCOUNTS,
    GRPC_RESUBSCRIBE_INTERVAL
)

from state import (
    GRPC_POSITION_PRICES,
    POSITION_WATCH_ACCOUNTS,
    PRIORITY_SCAN_QUEUE,
    PRIORITY_SCAN_SET,
    TOKEN_MEMORY,
    TRACKED_CANDIDATES
)

from filters.contracts import (
    is_excluded_contract_address
)


BASE58_ALPHABET = set(
    "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
)


def is_solana_pubkey(value):

    text = str(value or "").strip()

    if len(text) < 32 or len(text) > 44:
        return False

    return all(
        char in BASE58_ALPHABET
        for char in text
    )


def safe_float(
    value,
    default=0
):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def normalize_grpc_target(endpoint):

    parsed = urlparse(endpoint)

    if parsed.netloc:
        return parsed.netloc

    return endpoint.replace(
        "https://",
        ""
    ).replace(
        "http://",
        ""
    )


class YellowstoneImpulseListener:

    def __init__(self):

        self.enabled = (
            ENABLE_ALCHEMY_GRPC
            and ALCHEMY_GRPC_ENDPOINT
            and ALCHEMY_GRPC_X_TOKEN
        )

        self.request_queue = None

        self.account_to_token = {}

        self.last_account_key = None

        self.disabled_reason = None

        self.seen_signatures = []

    async def run(self):

        if not self.enabled:
            print(
                "Alchemy gRPC disabled "
                "or missing endpoint/token."
            )
            # This task is supervised by main() under asyncio.wait(
            # FIRST_COMPLETED); returning here would trip a full scanner
            # shutdown+restart. Idle forever instead so the scanner runs
            # normally without the gRPC stream.
            await asyncio.Event().wait()
            return

        try:
            import grpc
            import geyser_pb2
            import geyser_pb2_grpc
        except ImportError as e:
            print(
                "Alchemy gRPC dependencies are missing: "
                f"{e}. Generate geyser_pb2.py/"
                "geyser_pb2_grpc.py from Yellowstone "
                "proto files and install grpcio."
            )
            await asyncio.Event().wait()
            return

        target = normalize_grpc_target(
            ALCHEMY_GRPC_ENDPOINT
        )

        while True:

            try:
                await self.connect_and_stream(
                    grpc,
                    geyser_pb2,
                    geyser_pb2_grpc,
                    target
                )

            except grpc.aio.AioRpcError as e:
                if self.should_disable_for_rpc_error(e):
                    self.disabled_reason = (
                        f"{e.code().name}: {e.details()}"
                    )
                    print(
                        "Alchemy gRPC disabled after stream error: "
                        f"{self.disabled_reason}"
                    )
                    return

                print(
                    "Alchemy gRPC stream error: "
                    f"{e.code().name}: {e.details()}"
                )

            except Exception as e:
                print(
                    f"Alchemy gRPC stream error: {e}"
                )

            await asyncio.sleep(5)

    async def connect_and_stream(
        self,
        grpc,
        geyser_pb2,
        geyser_pb2_grpc,
        target
    ):

        credentials = grpc.ssl_channel_credentials()

        async with grpc.aio.secure_channel(
            target,
            credentials
        ) as channel:

            stub = geyser_pb2_grpc.GeyserStub(
                channel
            )

            self.request_queue = asyncio.Queue()

            await self.enqueue_subscribe_request(
                geyser_pb2
            )

            refresher = asyncio.create_task(
                self.refresh_subscriptions(
                    geyser_pb2
                )
            )

            try:
                stream = stub.Subscribe(
                    self.request_iterator(),
                    metadata=(
                        (
                            "x-token",
                            ALCHEMY_GRPC_X_TOKEN
                        ),
                    )
                )

                async for update in stream:

                    if self.is_ping(update):
                        await self.enqueue_ping(
                            geyser_pb2
                        )
                        continue

                    self.handle_update(update)

            finally:
                refresher.cancel()

                try:
                    await self.request_queue.put(None)
                except Exception:
                    pass

                try:
                    await refresher
                except asyncio.CancelledError:
                    pass

    async def request_iterator(self):

        while True:
            request = await self.request_queue.get()

            if request is None:
                return

            yield request

    @staticmethod
    def should_disable_for_rpc_error(error):

        code = error.code()
        code_name = getattr(
            code,
            "name",
            ""
        )

        return code_name in {
            "UNAUTHENTICATED",
            "PERMISSION_DENIED",
            "UNIMPLEMENTED"
        }

    async def refresh_subscriptions(
        self,
        geyser_pb2
    ):

        while True:
            await asyncio.sleep(
                GRPC_RESUBSCRIBE_INTERVAL
            )

            await self.enqueue_subscribe_request(
                geyser_pb2
            )

    async def enqueue_subscribe_request(
        self,
        geyser_pb2
    ):

        account_to_token = {}

        fallback_mints = {}

        for token_address, metadata in TRACKED_CANDIDATES.items():

            if (
                str(metadata.get("chain", "solana")).lower()
                != "solana"
            ):
                continue

            if is_excluded_contract_address(
                token_address
            ):
                continue

            if not is_solana_pubkey(token_address):
                continue

            fallback_mints[token_address] = token_address

            pair_address = metadata.get(
                "pair_address"
            )

            if pair_address and is_solana_pubkey(pair_address):
                account_to_token[pair_address] = token_address

        # Always include open-position pool accounts so we get real-time
        # swap prices for stop-loss monitoring.
        for pair_addr, token_addr in POSITION_WATCH_ACCOUNTS.items():
            if is_solana_pubkey(pair_addr):
                account_to_token[pair_addr] = token_addr

        if len(account_to_token) < GRPC_MAX_WATCH_ACCOUNTS:

            remaining = (
                GRPC_MAX_WATCH_ACCOUNTS
                - len(account_to_token)
            )

            for mint, token_address in list(
                fallback_mints.items()
            )[:remaining]:
                account_to_token[mint] = token_address

        accounts = sorted(
            account_to_token.keys()
        )[:GRPC_MAX_WATCH_ACCOUNTS]

        account_to_token = {
            account: account_to_token[account]
            for account in accounts
        }

        accounts = sorted(
            account_to_token.keys()
        )

        account_key = tuple(accounts)

        if account_key == self.last_account_key:
            return

        self.account_to_token = account_to_token
        self.last_account_key = account_key

        if not accounts:
            return

        tx_filter = (
            geyser_pb2.SubscribeRequestFilterTransactions(
                vote=False,
                failed=False,
                account_include=accounts
            )
        )

        request = geyser_pb2.SubscribeRequest(
            transactions={
                "tracked_organic_candidates": tx_filter
            },
            commitment=self.confirmed_commitment(
                geyser_pb2
            )
        )

        await self.request_queue.put(request)

        print(
            "Alchemy gRPC watching "
            f"{len(accounts)} tracked accounts."
        )

    async def enqueue_ping(
        self,
        geyser_pb2
    ):

        ping_type = getattr(
            geyser_pb2,
            "SubscribeRequestPing",
            None
        )

        if not ping_type:
            return

        await self.request_queue.put(
            geyser_pb2.SubscribeRequest(
                ping=ping_type(id=1)
            )
        )

    def handle_update(self, update):

        now = time.time()
        trade_flows = self.extract_trade_flows(update)

        for flow in trade_flows:
            token_address = flow.get("token_address")

            if not token_address:
                continue

            memory = TOKEN_MEMORY[token_address]

            memory["last_grpc_activity"] = now
            self.record_trade_flow(
                memory,
                flow,
                now
            )

        for account in self.extract_accounts(update):

            token_address = self.account_to_token.get(
                account
            )

            if not token_address:
                continue

            if is_excluded_contract_address(
                token_address
            ):
                continue

            memory = TOKEN_MEMORY[token_address]

            memory[
                "last_grpc_activity"
            ] = now

            if token_address in PRIORITY_SCAN_SET:
                continue

            PRIORITY_SCAN_QUEUE.append(
                token_address
            )

            PRIORITY_SCAN_SET.add(
                token_address
            )

    def record_trade_flow(
        self,
        memory,
        flow,
        now
    ):

        flows = memory.setdefault(
            "recent_trade_flows",
            []
        )

        flows.append(
            {
                "timestamp": now,
                "signature": flow.get("signature", ""),
                "token_address": flow.get("token_address", ""),
                "base_delta": flow.get("base_delta", 0),
                "direction": flow.get("direction", "unknown")
            }
        )

        cutoff = now - 3600
        memory["recent_trade_flows"] = [
            item
            for item in flows
            if safe_float(item.get("timestamp"), 0) >= cutoff
        ][-500:]
        memory["last_trade_flow_at"] = now

        self.maybe_update_position_price(flow, now)

    def maybe_update_position_price(self, flow, now):
        """Derive an implied price from gRPC swap data for open positions."""
        token_address = flow.get("token_address", "")
        if not token_address:
            return

        position_tokens = set(POSITION_WATCH_ACCOUNTS.values())
        if token_address not in position_tokens:
            return

        base_delta = float(flow.get("base_delta") or 0)
        sol_delta = flow.get("sol_delta")

        if sol_delta is None or abs(base_delta) < 1e-12:
            return

        # Reject if SOL direction doesn't match token direction
        # (buy: sol_delta<0, base_delta>0 | sell: sol_delta>0, base_delta<0)
        direction = flow.get("direction", "unknown")
        if direction == "buy" and sol_delta >= 0:
            return
        if direction == "sell" and sol_delta <= 0:
            return

        price_sol = abs(sol_delta) / abs(base_delta)

        # Sanity bounds: reject anything implausible
        if not (1e-18 < price_sol < 1e6):
            return

        GRPC_POSITION_PRICES[token_address] = {
            "price_sol": price_sol,
            "updated_at": now,
            "direction": direction,
        }

    def extract_trade_flows(self, update):

        tx_update = getattr(
            update,
            "transaction",
            None
        )

        if not tx_update:
            return []

        tx_info = getattr(
            tx_update,
            "transaction",
            None
        )

        if not tx_info:
            return []

        raw_tx = getattr(
            tx_info,
            "transaction",
            None
        )

        transaction = getattr(
            raw_tx,
            "transaction",
            raw_tx
        )

        message = getattr(
            transaction,
            "message",
            None
        )

        meta = getattr(
            tx_info,
            "meta",
            None
        )

        if not message or not meta:
            return []

        signature = self.extract_signature(
            transaction
        )

        if signature and signature in self.seen_signatures:
            return []

        if signature:
            self.seen_signatures.append(signature)
            self.seen_signatures = self.seen_signatures[-2000:]

        signers = self.extract_signers(
            message
        )
        if not signers:
            return []

        watched_tokens = set(
            self.account_to_token.values()
        )

        if not watched_tokens:
            return []

        pre_balances = self.index_token_balances(
            getattr(
                meta,
                "pre_token_balances",
                []
            )
        )
        post_balances = self.index_token_balances(
            getattr(
                meta,
                "post_token_balances",
                []
            )
        )

        sol_delta = self.extract_signer_sol_delta(message, meta)

        flows = []

        for index in sorted(
            set(pre_balances) | set(post_balances)
        ):
            pre_balance = pre_balances.get(index)
            post_balance = post_balances.get(index)
            balance = post_balance or pre_balance

            mint = self.token_balance_mint(
                balance
            )

            if mint not in watched_tokens:
                continue

            owner = self.token_balance_owner(
                balance
            )

            if owner not in signers:
                continue

            pre_amount = self.token_balance_amount(
                pre_balance
            )
            post_amount = self.token_balance_amount(
                post_balance
            )
            delta = post_amount - pre_amount

            if delta == 0:
                continue

            flows.append(
                {
                    "signature": signature or "",
                    "token_address": mint,
                    "base_delta": delta,
                    "sol_delta": sol_delta,
                    "direction": (
                        "buy"
                        if delta > 0
                        else "sell"
                    )
                }
            )

        return flows

    def extract_signer_sol_delta(self, message, meta):
        """Return net SOL exchanged by the signer in this transaction (in SOL).

        Negative = signer bought tokens (paid SOL).
        Positive = signer sold tokens (received SOL).
        Returns None if balance data is unavailable or ambiguous.
        """
        pre_sol_balances = list(
            getattr(meta, "pre_balances", [])
        )
        post_sol_balances = list(
            getattr(meta, "post_balances", [])
        )

        if not pre_sol_balances or not post_sol_balances:
            return None

        try:
            pre = int(pre_sol_balances[0])
            post = int(post_sol_balances[0])
        except (IndexError, TypeError, ValueError):
            return None

        fee = int(getattr(meta, "fee", 0) or 0)

        # raw_delta is negative for buys (SOL spent + fee deducted).
        # Adding fee back gives only the swap portion.
        raw_delta = post - pre
        net_lamports = raw_delta + fee
        return net_lamports / 1_000_000_000.0

    def extract_signature(self, transaction):

        signatures = getattr(
            transaction,
            "signatures",
            []
        )

        if not signatures:
            return ""

        first = signatures[0]

        return self.encode_pubkey(first) or str(first)

    def extract_signers(self, message):

        account_keys = getattr(
            message,
            "account_keys",
            []
        )
        header = getattr(
            message,
            "header",
            None
        )

        signer_count = int(
            getattr(
                header,
                "num_required_signatures",
                0
            )
            or 0
        )

        if signer_count <= 0:
            signer_count = 1

        signers = set()

        for key in account_keys[:signer_count]:
            encoded = self.encode_pubkey(key)

            if encoded:
                signers.add(encoded)

        return signers

    def index_token_balances(self, balances):

        indexed = {}

        for balance in balances or []:
            index = getattr(
                balance,
                "account_index",
                None
            )

            if index is None:
                continue

            indexed[int(index)] = balance

        return indexed

    def token_balance_mint(self, balance):

        if not balance:
            return ""

        return self.encode_pubkey(
            getattr(
                balance,
                "mint",
                ""
            )
        ) or ""

    def token_balance_owner(self, balance):

        if not balance:
            return ""

        return self.encode_pubkey(
            getattr(
                balance,
                "owner",
                ""
            )
        ) or ""

    def token_balance_amount(self, balance):

        if not balance:
            return 0

        amount = getattr(
            balance,
            "ui_token_amount",
            None
        )

        if amount is None:
            return 0

        ui_amount = getattr(
            amount,
            "ui_amount",
            None
        )

        if ui_amount is not None:
            try:
                return float(ui_amount)
            except (TypeError, ValueError):
                pass

        ui_amount_string = getattr(
            amount,
            "ui_amount_string",
            ""
        )

        try:
            return float(ui_amount_string or 0)
        except (TypeError, ValueError):
            return 0

    def extract_accounts(self, update):

        tx_update = getattr(
            update,
            "transaction",
            None
        )

        if not tx_update:
            return []

        tx_info = getattr(
            tx_update,
            "transaction",
            None
        )

        if not tx_info:
            return []

        raw_tx = getattr(
            tx_info,
            "transaction",
            None
        )

        transaction = getattr(
            raw_tx,
            "transaction",
            raw_tx
        )

        message = getattr(
            transaction,
            "message",
            None
        )

        if not message:
            return []

        accounts = []

        for key in getattr(
            message,
            "account_keys",
            []
        ):
            encoded = self.encode_pubkey(key)

            if encoded:
                accounts.append(encoded)

        meta = getattr(
            tx_info,
            "meta",
            None
        )

        for field in (
            "loaded_writable_addresses",
            "loaded_readonly_addresses"
        ):

            for key in getattr(
                meta,
                field,
                []
            ):
                encoded = self.encode_pubkey(key)

                if encoded:
                    accounts.append(encoded)

        return accounts

    def encode_pubkey(self, value):

        if isinstance(value, str):
            return value

        try:
            import base58
            return base58.b58encode(value).decode(
                "ascii"
            )
        except Exception:
            return None

    def is_ping(self, update):

        try:
            return (
                update.WhichOneof("update_oneof")
                == "ping"
            )
        except Exception:
            return False

    def confirmed_commitment(
        self,
        geyser_pb2
    ):

        enum_type = getattr(
            geyser_pb2,
            "CommitmentLevel",
            None
        )

        if enum_type and hasattr(
            enum_type,
            "CONFIRMED"
        ):
            return enum_type.CONFIRMED

        return getattr(
            geyser_pb2,
            "CONFIRMED",
            1
        )
