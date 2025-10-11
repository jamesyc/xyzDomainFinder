#!/usr/bin/env python3
"""Check availability of numeric .xyz domains.

The script queries the CentralNic RDAP service to determine whether 6 or
7 digit numeric .xyz domains are available. Results are written to a CSV
file so the process can be resumed and the output is append-only.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import aiohttp

try:
    from aiohttp import TCPConnector
except ImportError:  # pragma: no cover - compatibility fallback
    TCPConnector = None  # type: ignore[assignment]

RDAP_URL_TEMPLATE = "https://rdap.centralnic.com/xyz/domain/{domain}"
DEFAULT_RESUME_FILE = "resume_state.json"
DEFAULT_OUTPUT_FILE = "available_domains.csv"


class RateLimiter:
    """Simple cooperative rate limiter with optional minimum interval."""

    def __init__(self, rate: Optional[float] = None) -> None:
        self._lock = asyncio.Lock()
        self._next_time = 0.0
        self._min_interval = 0.0
        if rate and rate > 0:
            self._min_interval = 1.0 / rate

    async def acquire(self) -> None:
        if self._min_interval == 0 and self._next_time == 0:
            return
        loop = asyncio.get_running_loop()
        while True:
            async with self._lock:
                now = loop.time()
                wait = self._next_time - now
                if wait <= 0:
                    next_time = max(self._next_time, now)
                    if self._min_interval:
                        next_time += self._min_interval
                    self._next_time = next_time
                    return
            await asyncio.sleep(wait)

    async def postpone(self, delay: float) -> None:
        if delay <= 0:
            return
        loop = asyncio.get_running_loop()
        async with self._lock:
            self._next_time = max(self._next_time, loop.time() + delay)


def _parse_retry_after(headers: "aiohttp.typedefs.LooseHeaders") -> Optional[float]:
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delay = (parsed - now).total_seconds()
        return delay if delay > 0 else None


class ResumeState:
    """Handle progress persistence for resumable scans."""

    def __init__(self, lengths: Iterable[int], path: Path) -> None:
        self.path = path
        self.progress: Dict[str, int] = {str(length): 0 for length in lengths}
        self.lengths = [int(k) for k in self.progress.keys()]
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise RuntimeError(f"Could not parse resume file {path}: {exc}")
            stored = data.get("progress", {})
            for length in self.progress:
                if length in stored and isinstance(stored[length], int):
                    self.progress[length] = stored[length]

    def next_index(self, length: int) -> int:
        return self.progress[str(length)]

    def mark_processed(self, length: int, next_index: int) -> None:
        self.progress[str(length)] = next_index

    def save(self) -> None:
        payload = {"progress": self.progress}
        self.path.write_text(json.dumps(payload, indent=2))


THROTTLE_NOTICE_SHOWN = False


async def query_domain(session: aiohttp.ClientSession, domain: str, *,
                       max_retries: int, timeout: float, backoff: float,
                       rate_limiter: "RateLimiter") -> bool:
    """Return True if the domain is available, False if registered."""

    url = RDAP_URL_TEMPLATE.format(domain=domain)
    last_error: Optional[str] = None
    for attempt in range(1, max_retries + 1):
        try:
            await rate_limiter.acquire()
            async with session.get(url, timeout=timeout) as response:
                text = await response.text()
                if response.status == 200:
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"Invalid JSON for {domain}: {exc}\n{text[:200]}"
                        )
                    # Some RDAP deployments return a 200 with an empty or missing
                    # status array when the domain is unregistered. Treat that
                    # case as available while any status values indicate the name
                    # is taken or otherwise unavailable.
                    statuses = payload.get("status") or []
                    return not bool(statuses)
                if response.status == 404:
                    return True
                if response.status in {403, 429, 500, 502, 503, 504}:
                    retry_after = _parse_retry_after(response.headers)
                    last_error = f"HTTP {response.status}"
                    global THROTTLE_NOTICE_SHOWN
                    if not THROTTLE_NOTICE_SHOWN:
                        detail = (
                            f" (retrying after {retry_after:.1f}s)"
                            if retry_after is not None
                            else ""
                        )
                        print(
                            f"Encountered throttling response {response.status}{detail};"
                            " pausing before retrying...",
                            file=sys.stderr,
                        )
                        THROTTLE_NOTICE_SHOWN = True
                    if retry_after is not None and retry_after > 0:
                        await rate_limiter.postpone(retry_after)
                        await asyncio.sleep(retry_after)
                    else:
                        delay = backoff * attempt
                        await rate_limiter.postpone(delay)
                        await asyncio.sleep(delay)
                    continue
                # Unexpected status codes should raise for visibility.
                raise RuntimeError(
                    f"Unexpected status {response.status} for {domain}: {text[:200]}"
                )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = f"{exc.__class__.__name__}: {exc}"
            if attempt == max_retries:
                raise
            await asyncio.sleep(backoff * attempt)
    raise RuntimeError(
        f"Failed to determine availability for {domain} after {max_retries} attempts"
        + (f" ({last_error})" if last_error else "")
    )


async def process_batch(session: aiohttp.ClientSession, numbers: List[int], *,
                        length: int, semaphore: asyncio.Semaphore,
                        max_retries: int, timeout: float, backoff: float,
                        rate_limiter: "RateLimiter") -> List[str]:
    async def worker(number: int) -> Optional[str]:
        domain = f"{number:0{length}d}.xyz"
        async with semaphore:
            is_available = await query_domain(
                session,
                domain,
                max_retries=max_retries,
                timeout=timeout,
                backoff=backoff,
                rate_limiter=rate_limiter,
            )
        return domain if is_available else None

    tasks = [asyncio.create_task(worker(num)) for num in numbers]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    available: List[str] = []
    for number, result in zip(numbers, results):
        if isinstance(result, Exception):
            raise RuntimeError(
                f"Failed to check {number:0{length}d}.xyz: {result}"
            ) from result
        if result:
            available.append(result)
    return available


async def scan_domains(*, lengths: List[int], batch_size: int, concurrency: int,
                       resume: ResumeState, output_path: Path, max_count: Optional[int],
                       max_retries: int, timeout: float, backoff: float,
                       save_every: int, rate_limit: Optional[float]) -> None:
    semaphore = asyncio.Semaphore(concurrency)
    checked = 0
    save_counter = 0
    rate_limiter = RateLimiter(rate_limit)

    connector = None
    if TCPConnector is not None:
        connector = TCPConnector(limit=concurrency, limit_per_host=concurrency)

    async with aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": "xyz-domain-checker/1.0"},
        trust_env=True,
    ) as session:
        with output_path.open("a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            if csvfile.tell() == 0:
                writer.writerow(["domain", "length"])
                csvfile.flush()

            for length in lengths:
                max_value = 10 ** length
                index = resume.next_index(length)
                while index < max_value:
                    if max_count is not None and checked >= max_count:
                        resume.save()
                        return
                    remaining = max_value - index
                    if max_count is not None:
                        remaining = min(remaining, max_count - checked)
                    current_batch = list(range(index, index + min(batch_size, remaining)))
                    available = await process_batch(
                        session,
                        current_batch,
                        length=length,
                        semaphore=semaphore,
                        max_retries=max_retries,
                        timeout=timeout,
                        backoff=backoff,
                        rate_limiter=rate_limiter,
                    )
                    for domain in available:
                        writer.writerow([domain, length])
                    csvfile.flush()
                    checked += len(current_batch)
                    index += len(current_batch)
                    resume.mark_processed(length, index)
                    save_counter += len(current_batch)
                    if save_counter >= save_every:
                        resume.save()
                        print(
                            f"Processed {index:,}/{max_value:,} combinations for {length}-digit domains",
                            flush=True,
                        )
                        save_counter = 0
                resume.save()
                print(
                    f"Completed scan for {length}-digit domains (total checked: {index:,})",
                    flush=True,
                )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan numeric .xyz domain availability.")
    parser.add_argument(
        "--length",
        "-l",
        dest="lengths",
        action="append",
        type=int,
        choices=[6, 7],
        help="Digit length to scan (default: 6 and 7)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(DEFAULT_OUTPUT_FILE),
        help="CSV file to write available domains to.",
    )
    parser.add_argument(
        "--resume-file",
        type=Path,
        default=Path(DEFAULT_RESUME_FILE),
        help="Path to resume checkpoint file.",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Ignore existing output/resume files and start fresh.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Number of domains to check per asynchronous batch.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Maximum concurrent RDAP requests.",
    )
    parser.add_argument(
        "--max-count",
        type=int,
        help="Limit the total number of domains checked (useful for testing).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Number of times to retry a failed RDAP request.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--backoff",
        type=float,
        default=1.5,
        help="Backoff multiplier used between retries.",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        help="Maximum RDAP requests per second (default: unlimited).",
    )
    parser.add_argument(
        "--save-every",
        type=int,
        default=200,
        help="Persist resume state after this many checks.",
    )
    args = parser.parse_args(argv)
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    if args.concurrency <= 0:
        parser.error("--concurrency must be positive")
    if args.max_count is not None and args.max_count <= 0:
        parser.error("--max-count must be positive")
    if args.save_every <= 0:
        parser.error("--save-every must be positive")
    if args.rate_limit is not None and args.rate_limit <= 0:
        parser.error("--rate-limit must be positive")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    lengths = args.lengths or [6, 7]

    resume_path = args.resume_file
    output_path = args.output

    if args.restart:
        if resume_path.exists():
            resume_path.unlink()
        if output_path.exists():
            output_path.unlink()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    resume = ResumeState(lengths, resume_path)

    try:
        asyncio.run(
            scan_domains(
                lengths=lengths,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                resume=resume,
                output_path=output_path,
                max_count=args.max_count,
                max_retries=args.max_retries,
                timeout=args.timeout,
                backoff=args.backoff,
                save_every=args.save_every,
                rate_limit=args.rate_limit,
            )
        )
    except KeyboardInterrupt:
        print("Interrupted, progress saved.", file=sys.stderr)
        resume.save()
        return 1
    except Exception as exc:
        resume.save()
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
