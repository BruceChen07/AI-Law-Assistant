from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run local pre-release smoke validation.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--backend-port", type=int, default=18001)
    parser.add_argument("--frontend-port", type=int, default=14173)
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--admin-password", default="admin123")
    return parser.parse_args()


def find_python(repo_root: Path) -> str:
    candidates = [
        repo_root / "app" / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    raise FileNotFoundError("python executable not found for smoke test")


def find_npm() -> str:
    for name in ("npm.cmd", "npm"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise FileNotFoundError("npm executable not found")


def wait_for_http(url: str, timeout_seconds: int = 90) -> tuple[bool, str]:
    deadline = time.time() + timeout_seconds
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return True, f"http={response.status}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(1)
    return False, last_error


def request_json(url: str, method: str = "GET", headers: dict[str, str] | None = None, body: dict | None = None) -> dict:
    payload = None
    request_headers = headers.copy() if headers else {}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        url, data=payload, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def request_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=20) as response:
        return response.read().decode("utf-8", errors="ignore")


def terminate_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    backend_log = output_dir / "backend-smoke.log"
    frontend_log = output_dir / "frontend-smoke.log"
    summary_path = output_dir / "smoke-summary.json"

    python_exe = find_python(repo_root)
    npm_exe = find_npm()

    admin_cmd = [
        python_exe,
        str((repo_root / "bin" / "create_admin.py").resolve()),
        "--username",
        args.admin_username,
        "--password",
        args.admin_password,
    ]
    subprocess.run(admin_cmd, cwd=str(repo_root), check=True,
                   capture_output=True, text=True)

    env = os.environ.copy()
    env.update(
        {
            "APP_PORT": str(args.backend_port),
            "APP_PORT_AUTO_SWITCH": "0",
            "PYTHONUNBUFFERED": "1",
            "VITE_API_BASE": f"http://127.0.0.1:{args.backend_port}",
        }
    )

    backend_proc = None
    frontend_proc = None
    steps: list[dict[str, str | int | bool]] = []
    try:
        backend_handle = backend_log.open("w", encoding="utf-8")
        backend_proc = subprocess.Popen(
            [python_exe, "-m", "uvicorn", "app.main:app", "--host",
                "127.0.0.1", "--port", str(args.backend_port)],
            cwd=str(repo_root),
            env=env,
            stdout=backend_handle,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        frontend_handle = frontend_log.open("w", encoding="utf-8")
        frontend_proc = subprocess.Popen(
            [npm_exe, "run", "preview", "--", "--host",
                "127.0.0.1", "--port", str(args.frontend_port)],
            cwd=str((repo_root / "web").resolve()),
            env=env,
            stdout=frontend_handle,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        ok, detail = wait_for_http(
            f"http://127.0.0.1:{args.backend_port}/health")
        steps.append({"name": "backend_health", "ok": ok, "detail": detail})
        if not ok:
            raise RuntimeError(f"backend health check failed: {detail}")

        ok, detail = wait_for_http(f"http://127.0.0.1:{args.frontend_port}/")
        steps.append({"name": "frontend_preview", "ok": ok, "detail": detail})
        if not ok:
            raise RuntimeError(f"frontend preview check failed: {detail}")

        health = request_json(f"http://127.0.0.1:{args.backend_port}/health")
        steps.append({"name": "health_payload", "ok": health.get(
            "status") == "ok", "detail": json.dumps(health, ensure_ascii=False)})

        login = request_json(
            f"http://127.0.0.1:{args.backend_port}/api/auth/login",
            method="POST",
            body={"username": args.admin_username,
                  "password": args.admin_password},
        )
        token = str(login.get("access_token") or "")
        steps.append({"name": "auth_login", "ok": bool(token),
                     "detail": login.get("user", {}).get("username", "")})
        if not token:
            raise RuntimeError("smoke login returned empty access token")

        me = request_json(
            f"http://127.0.0.1:{args.backend_port}/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        steps.append({"name": "auth_me", "ok": me.get("username")
                     == args.admin_username, "detail": me.get("role", "")})

        regulations = request_json(
            f"http://127.0.0.1:{args.backend_port}/regulations")
        steps.append({"name": "regulations_list", "ok": isinstance(regulations, list),
                     "detail": f"count={len(regulations) if isinstance(regulations, list) else -1}"})

        ui_config = request_json(
            f"http://127.0.0.1:{args.backend_port}/contracts/ui-config",
            headers={"Authorization": f"Bearer {token}"},
        )
        steps.append({"name": "contracts_ui_config", "ok": isinstance(
            ui_config, dict), "detail": json.dumps(ui_config, ensure_ascii=False)})

        html = request_text(f"http://127.0.0.1:{args.frontend_port}/")
        html_ok = "html" in html.lower()
        steps.append({"name": "frontend_html", "ok": html_ok,
                     "detail": f"length={len(html)}"})
        if not html_ok:
            raise RuntimeError("frontend preview did not return html content")

        summary = {"status": "passed", "steps": steps}
        summary_path.write_text(json.dumps(
            summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(summary_path)
        return 0
    except Exception as exc:  # noqa: BLE001
        summary = {"status": "failed", "error": str(exc), "steps": steps}
        summary_path.write_text(json.dumps(
            summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(summary_path)
        return 2
    finally:
        terminate_process(frontend_proc)
        terminate_process(backend_proc)


if __name__ == "__main__":
    sys.exit(main())
