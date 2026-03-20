"""
Async Bridge.
职责: 提供同步代码调用异步协程的桥接工具，处理事件循环的管理。
输入输出: 接收一个协程对象，返回协程的执行结果。
异常场景: 协程执行过程中的异常会被捕获并在主线程重新抛出。
"""
import asyncio
import threading
import structlog
from typing import Dict, Any, Coroutine

logger = structlog.get_logger(__name__)

def run_coro_sync(coro: Coroutine) -> Any:
    """在同步上下文中运行异步协程。"""
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
