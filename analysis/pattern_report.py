import argparse
import asyncio
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alerts.telegram import TelegramAlertSender  # noqa: E402
from analysis.pattern_analyzer import LLMPatternAnalyzer  # noqa: E402
from config import (  # noqa: E402
    LLM_PATTERN_REPORT_LOOKBACK_HOURS,
    LLM_PATTERN_REPORT_MIN_ALERTS
)
from storage.sqlite import ScannerStorage  # noqa: E402


async def run(args):

    storage = ScannerStorage()
    await storage.initialize()

    now = time.time()
    lookback_hours = args.hours
    since = now - lookback_hours * 3600

    alert_report = await storage.build_ignition_alert_report(
        now,
        since=since
    )
    alert_count = alert_report["summary"].get(
        "alerts",
        0
    )

    if alert_count < args.min_alerts:
        print(
            "Not enough alerts for an LLM pattern report: "
            f"{alert_count}/{args.min_alerts}"
        )
        return

    analyzer = LLMPatternAnalyzer()

    if not analyzer.ready():
        print("LLM is not configured.")
        return

    llm_report = await analyzer.analyze(
        alert_report.get("alerts", []),
        alert_report.get("summary", {}),
        lookback_hours
    )

    if not llm_report:
        print("No report returned by LLM.")
        return

    print(llm_report["text"])

    if args.send:
        telegram = TelegramAlertSender()
        delivered = await telegram.send_llm_pattern_report(
            llm_report.get("html")
            or llm_report["text"]
        )
        await storage.record_llm_pattern_report(
            llm_report.get("provider"),
            llm_report.get("model"),
            lookback_hours,
            alert_count,
            llm_report.get("text"),
            raw_payload={
                "parsed": llm_report.get("parsed"),
                "html": llm_report.get("html"),
                "delivered": delivered
            },
            created_at=now
        )


def main():

    parser = argparse.ArgumentParser(
        description="Generate an LLM narrative pattern report."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=LLM_PATTERN_REPORT_LOOKBACK_HOURS
    )
    parser.add_argument(
        "--min-alerts",
        type=int,
        default=LLM_PATTERN_REPORT_MIN_ALERTS
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send the report to the configured Telegram groups."
    )

    asyncio.run(
        run(parser.parse_args())
    )


if __name__ == "__main__":
    main()
