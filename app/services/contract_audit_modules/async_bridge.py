"""
Async Bridge.
Responsibilities: Provides a bridge for synchronous code to call asynchronous coroutines, handling event loop management.
Input/Output: Accepts a coroutine object and returns the result of the coroutine.
Exception Handling: Returns the coroutine result or throws the exception in the main thread.
"""
import asyncio
import threading
import structlog
from typing import Dict, Any, Coroutine

logger = structlog.get_logger(__name__)


def run_coro_sync(coro: Coroutine) -> Any:
    """Run an asynchronous coroutine in a synchronous context."""
    try:
        asyncio.get_running_loop()
        has_running = True
    except RuntimeError:
        has_running = False
    if not has_running:
        return asyncio.run(coro)

    result_box: Dict[str, Any] = {}
    error_box: Dict[str, Exception] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result_box["value"] = loop.run_until_complete(coro)
        except Exception as e:
            error_box["error"] = e
        finally:
            loop.close()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")
