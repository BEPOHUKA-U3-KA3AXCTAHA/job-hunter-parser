"""Claude CLI subprocess pool — runs LLM calls through the user's Claude
subscription (free for paid users, no per-token API charges).

Each call spawns `claude --model <model> -p '<prompt>'` and reads stdout.
Subprocess overhead is ~5-30 sec per call, so we run a small concurrent
worker pool (default 5) to cut wall-clock time for batch generation.

Usage:
    pool = ClaudeCLIPool(workers=5, model="claude-haiku-4-5")
    results = await pool.batch_generate([
        ("system prompt", "user prompt 1"),
        ("system prompt", "user prompt 2"),
        ...
    ])

Rate-limit aware: a Max plan typically tolerates 50-200 messages/hour. We
default to a soft cap at 60/minute via a token bucket; tune per your plan.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from loguru import logger


@dataclass
class LLMResult:
    """One subprocess call outcome."""
    ok: bool
    text: str
    error: str | None = None
    duration_s: float = 0.0


class ClaudeCLIPool:
    """Async pool that runs `claude -p <prompt>` subprocess calls in parallel.

    Args:
        workers: max concurrent subprocesses (5 is safe default for Claude Max)
        model: model id, e.g. "claude-haiku-4-5" (cheap/fast) or "claude-sonnet-4-6"
        timeout_s: kill subprocess after this many seconds
        per_minute_cap: soft rate limit (token bucket); 60/min on Max is safe
        cli: path to the `claude` binary (auto-discovered if None)
    """

    def __init__(
        self,
        workers: int = 5,
        model: str = "claude-haiku-4-5",
        timeout_s: int = 90,
        per_minute_cap: int = 60,
        cli: str = "claude",
    ) -> None:
        self.workers = workers
        self.model = model
        self.timeout_s = timeout_s
        self.cli = cli
        self._sem = asyncio.Semaphore(workers)
        self._minute_window: list[float] = []
        self._cap = per_minute_cap
        self._lock = asyncio.Lock()

    async def _wait_for_slot(self) -> None:
        """Token-bucket-ish: ensure we don't exceed cap calls per rolling minute."""
        async with self._lock:
            now = time.monotonic()
            # drop calls older than 60s
            self._minute_window = [t for t in self._minute_window if now - t < 60.0]
            if len(self._minute_window) >= self._cap:
                wait = 60.0 - (now - self._minute_window[0]) + 0.1
                logger.info("Rate-limit pause {:.1f}s ({} calls in last minute, cap {})",
                            wait, len(self._minute_window), self._cap)
                await asyncio.sleep(wait)
                now = time.monotonic()
                self._minute_window = [t for t in self._minute_window if now - t < 60.0]
            self._minute_window.append(time.monotonic())

    async def _one_call(self, system: str, user: str) -> LLMResult:
        """Run a single `claude` subprocess and return the result."""
        await self._wait_for_slot()
        async with self._sem:
            start = time.monotonic()

            # `claude -p "user prompt"` runs a one-shot non-interactive prompt.
            # System prompt is prepended into the user prompt with a separator
            # because `claude` CLI doesn't expose a --system flag in 2.1.x.
            full_prompt = f"{system}\n\n---\n\n{user}" if system else user

            try:
                proc = await asyncio.create_subprocess_exec(
                    self.cli, "--model", self.model, "-p", full_prompt,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout_s,
                )
            except asyncio.TimeoutError:
                logger.warning("Claude CLI timeout after {}s", self.timeout_s)
                try:
                    proc.kill()
                except Exception:
                    pass
                return LLMResult(ok=False, text="", error="timeout", duration_s=self.timeout_s)
            except Exception as e:
                logger.error("Claude CLI exec failed: {}", e)
                return LLMResult(ok=False, text="", error=str(e), duration_s=time.monotonic() - start)

            duration = time.monotonic() - start
            if proc.returncode != 0:
                err = (stderr or b"").decode(errors="replace")[:500]
                logger.warning("Claude CLI exit {}: {}", proc.returncode, err)
                return LLMResult(ok=False, text="", error=err, duration_s=duration)

            text = (stdout or b"").decode(errors="replace").strip()
            return LLMResult(ok=True, text=text, duration_s=duration)

    async def batch_generate(
        self, prompts: list[tuple[str, str]],
    ) -> list[LLMResult]:
        """Run all prompts concurrently (up to workers in flight at once).

        prompts: list of (system_prompt, user_prompt) tuples
        """
        logger.info("Claude CLI pool: {} prompts × {} workers ({})",
                    len(prompts), self.workers, self.model)
        tasks = [asyncio.create_task(self._one_call(s, u)) for s, u in prompts]
        results = await asyncio.gather(*tasks)
        ok = sum(1 for r in results if r.ok)
        avg = sum(r.duration_s for r in results) / max(len(results), 1)
        logger.info("Claude CLI pool done: {}/{} ok, avg {:.1f}s/call", ok, len(results), avg)
        return results


if __name__ == "__main__":
    # Smoke test: 3 quick prompts in parallel
    import asyncio

    async def main():
        pool = ClaudeCLIPool(workers=3, model="claude-haiku-4-5")
        prompts = [
            ("You are concise.", "What's 2+2?"),
            ("You are concise.", "Capital of France?"),
            ("You are concise.", "Color of grass?"),
        ]
        results = await pool.batch_generate(prompts)
        for i, r in enumerate(results, 1):
            print(f"--- {i} (ok={r.ok}, {r.duration_s:.1f}s) ---")
            print(r.text[:200] if r.ok else f"ERROR: {r.error[:200]}")

    asyncio.run(main())
