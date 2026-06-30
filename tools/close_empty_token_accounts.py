#!/usr/bin/env python3
"""Close empty Solana SPL token accounts for the configured trading wallet.

Default mode is dry-run. Use --execute to sign and send close-account
transactions. The signing key is the Solana base58 wallet secret, not
GMGN_PRIVATE_KEY.
"""

import argparse
import asyncio
import base64
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)
sys.path.insert(0, str(REPO))

import aiohttp  # noqa: E402
import base58  # noqa: E402
from solders.hash import Hash  # noqa: E402
from solders.instruction import AccountMeta, Instruction  # noqa: E402
from solders.keypair import Keypair  # noqa: E402
from solders.message import MessageV0  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402
from solders.transaction import VersionedTransaction  # noqa: E402

from config import (  # noqa: E402
    DEFINITIVE_FLASH_PRIVATE_KEY,
    GMGN_TRADING_WALLET,
)
from trading.execution import solana_rpc_urls  # noqa: E402


TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
LAMPORTS_PER_SOL = 1_000_000_000


def load_keypair(secret):
    key_bytes = base58.b58decode(secret)
    if len(key_bytes) >= 64:
        return Keypair.from_bytes(key_bytes[:64])
    if len(key_bytes) == 32:
        return Keypair.from_seed(key_bytes)
    raise ValueError(f"unexpected Solana key length: {len(key_bytes)}")


async def rpc_call(session, urls, method, params):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }
    last_error = "rpc_unavailable"

    for url in urls:
        try:
            async with session.post(url, json=payload) as response:
                data = await response.json(content_type=None)
            if data.get("error"):
                last_error = data["error"].get("message", str(data["error"]))
                continue
            return data.get("result")
        except Exception as exc:
            last_error = str(exc)

    raise RuntimeError(last_error)


async def fetch_empty_accounts(session, urls, owner, include_native=False):
    rows = []

    for program_id in (TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID):
        result = await rpc_call(
            session,
            urls,
            "getTokenAccountsByOwner",
            [
                owner,
                {"programId": program_id},
                {"encoding": "jsonParsed", "commitment": "confirmed"},
            ],
        )
        for item in (result or {}).get("value", []):
            account = item.get("account") or {}
            parsed = (
                (account.get("data") or {})
                .get("parsed", {})
                .get("info", {})
            )
            token_amount = parsed.get("tokenAmount") or {}
            amount = str(token_amount.get("amount", ""))
            is_native = bool(parsed.get("isNative"))
            close_authority = parsed.get("closeAuthority", "")

            if amount != "0":
                continue
            if is_native and not include_native:
                continue
            if close_authority and close_authority != owner:
                continue

            rows.append({
                "pubkey": item.get("pubkey", ""),
                "mint": parsed.get("mint", ""),
                "owner": parsed.get("owner", ""),
                "close_authority": close_authority,
                "program_id": program_id,
                "lamports": int(account.get("lamports") or 0),
                "state": parsed.get("state", ""),
                "is_native": is_native,
            })

    return rows


def close_account_instruction(row, owner, destination):
    return Instruction(
        Pubkey.from_string(row["program_id"]),
        bytes([9]),
        [
            AccountMeta(Pubkey.from_string(row["pubkey"]), False, True),
            AccountMeta(Pubkey.from_string(destination), False, True),
            AccountMeta(Pubkey.from_string(owner), True, False),
        ],
    )


async def latest_blockhash(session, urls):
    result = await rpc_call(
        session,
        urls,
        "getLatestBlockhash",
        [{"commitment": "confirmed"}],
    )
    return (result.get("value") or {}).get("blockhash", "")


async def send_transaction(session, urls, tx):
    encoded = base64.b64encode(bytes(tx)).decode()
    return await rpc_call(
        session,
        urls,
        "sendTransaction",
        [
            encoded,
            {
                "encoding": "base64",
                "skipPreflight": False,
                "preflightCommitment": "confirmed",
                "maxRetries": 3,
            },
        ],
    )


async def confirm_signature(session, urls, signature, timeout_s=45):
    deadline = time.time() + max(timeout_s, 1)

    while time.time() <= deadline:
        result = await rpc_call(
            session,
            urls,
            "getSignatureStatuses",
            [[signature]],
        )
        value = ((result or {}).get("value") or [None])[0]
        if value:
            if value.get("err"):
                return False, str(value.get("err"))
            if value.get("confirmationStatus") in ("confirmed", "finalized"):
                return True, value.get("confirmationStatus")
        await asyncio.sleep(2.5)

    return False, "confirm_timeout"


def chunks(rows, size):
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


async def run(args):
    urls = solana_rpc_urls()
    if not urls:
        raise SystemExit("No Solana RPC URL configured")

    secret = os.getenv(args.private_key_env) or DEFINITIVE_FLASH_PRIVATE_KEY
    if not secret:
        raise SystemExit(f"Missing Solana key: {args.private_key_env}")

    keypair = load_keypair(secret)
    owner = args.owner or GMGN_TRADING_WALLET or str(keypair.pubkey())
    destination = args.destination or owner

    if str(keypair.pubkey()) != owner:
        raise SystemExit(
            "Signing key does not match owner wallet "
            f"({keypair.pubkey()} != {owner})"
        )

    timeout = aiohttp.ClientTimeout(total=args.rpc_timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        accounts = await fetch_empty_accounts(
            session,
            urls,
            owner,
            include_native=args.include_native,
        )

        if args.limit:
            accounts = accounts[:args.limit]

        total_lamports = sum(row["lamports"] for row in accounts)
        print(
            f"owner={owner} empty_accounts={len(accounts)} "
            f"estimated_reclaim_sol={total_lamports / LAMPORTS_PER_SOL:.6f} "
            f"rpc_primary={'helius' if 'helius-rpc.com' in urls[0] else 'other'}"
        )

        for row in accounts[:args.print_limit]:
            print(
                f"- {row['pubkey']} mint={row['mint']} "
                f"rent_sol={row['lamports'] / LAMPORTS_PER_SOL:.6f} "
                f"program={'token2022' if row['program_id'] == TOKEN_2022_PROGRAM_ID else 'token'}"
            )

        if not args.execute:
            print("dry_run=true; pass --execute to close these accounts")
            return

        if not accounts:
            print("nothing_to_close=true")
            return

        sent = []
        for batch in chunks(accounts, args.batch_size):
            blockhash = await latest_blockhash(session, urls)
            if not blockhash:
                raise RuntimeError("missing latest blockhash")

            instructions = [
                close_account_instruction(row, owner, destination)
                for row in batch
            ]
            message = MessageV0.try_compile(
                Pubkey.from_string(owner),
                instructions,
                [],
                Hash.from_string(blockhash),
            )
            tx = VersionedTransaction(message, [keypair])
            signature = await send_transaction(session, urls, tx)
            sent.append(signature)
            print(
                f"submitted batch_accounts={len(batch)} signature={signature}"
            )

            if not args.no_confirm:
                ok, status = await confirm_signature(
                    session,
                    urls,
                    signature,
                    timeout_s=args.confirm_timeout,
                )
                print(f"confirmed={ok} status={status} signature={signature}")
                if not ok:
                    raise RuntimeError(f"confirmation failed: {status}")

        print(f"closed_batches={len(sent)}")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="sign and submit close-account transactions")
    parser.add_argument("--owner", default="",
                        help="owner wallet; defaults to GMGN_TRADING_WALLET")
    parser.add_argument("--destination", default="",
                        help="rent destination; defaults to owner")
    parser.add_argument("--private-key-env", default="DEFINITIVE_FLASH_PRIVATE_KEY",
                        help="env var containing Solana base58 secret key")
    parser.add_argument("--batch-size", type=int, default=8,
                        help="token accounts closed per transaction")
    parser.add_argument("--limit", type=int, default=0,
                        help="limit accounts processed, useful for smoke tests")
    parser.add_argument("--print-limit", type=int, default=20,
                        help="max candidate accounts printed")
    parser.add_argument("--include-native", action="store_true",
                        help="also close empty native/wSOL accounts")
    parser.add_argument("--no-confirm", action="store_true",
                        help="do not wait for signature confirmation")
    parser.add_argument("--confirm-timeout", type=float, default=45.0)
    parser.add_argument("--rpc-timeout", type=float, default=30.0)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
