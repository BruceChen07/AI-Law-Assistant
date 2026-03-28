from app.app_factory import create_app, init_only
import os
import sys
import socket
import uvicorn

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


app = create_app()


def _is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _pick_port(default_port: int, max_tries: int = 50) -> int:
    for offset in range(max_tries + 1):
        candidate = default_port + offset
        if _is_port_available(candidate):
            return candidate
    raise RuntimeError(
        f"no available port found from {default_port} to {default_port + max_tries}")


def main():
    if "--init" in sys.argv:
        init_only()
        return
    requested_port = int(os.environ.get("APP_PORT", "8000"))
    auto_switch = os.environ.get("APP_PORT_AUTO_SWITCH", "1").strip().lower() not in {
        "0", "false", "no"}
    port = _pick_port(requested_port) if auto_switch else requested_port
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
