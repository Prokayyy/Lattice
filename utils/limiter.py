import asyncio
import time
from collections import deque


REQUEST_SEMAPHORE = asyncio.Semaphore(8)


class AsyncRateLimiter:

    def __init__(
        self,
        max_calls,
        period_seconds
    ):

        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self.calls = deque()
        self.lock = asyncio.Lock()

    async def wait(self):

        while True:

            async with self.lock:

                now = time.monotonic()

                while (
                    self.calls
                    and now - self.calls[0]
                    >= self.period_seconds
                ):
                    self.calls.popleft()

                if len(self.calls) < self.max_calls:
                    self.calls.append(now)
                    return

                sleep_for = (
                    self.period_seconds
                    - (now - self.calls[0])
                    + 0.05
                )

            await asyncio.sleep(
                max(sleep_for, 0.05)
            )
