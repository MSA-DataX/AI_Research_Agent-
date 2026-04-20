from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _ensure_model_loaded() -> None:
    model = os.getenv("MODEL_NAME", "qwen3-14b")
    try:
        out = subprocess.run(
            ["lms", "status"], capture_output=True, text=True, timeout=5
        )
    except FileNotFoundError:
        print("[start] 'lms' CLI nicht im PATH - Modell bitte manuell in LM Studio laden")
        return
    except Exception as e:
        print(f"[start] 'lms status' fehlgeschlagen: {e}")
        return

    text = (out.stdout or "") + (out.stderr or "")
    loaded_section = text.split("Loaded Models", 1)[-1]
    if model in loaded_section:
        print(f"[start] Modell '{model}' ist geladen")
        return

    print(f"[start] Lade Modell '{model}' (nicht in Loaded Models) ...")
    try:
        subprocess.run(["lms", "load", model], check=False, timeout=120)
    except Exception as e:
        print(f"[start] lms load fehlgeschlagen: {e}")


def _port_in_use(host: str, port: int) -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        result = s.connect_ex((host, port))
    finally:
        s.close()
    return result == 0


def _find_zombie_pid(port: int) -> int | None:
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line.upper():
            parts = line.split()
            try:
                return int(parts[-1])
            except Exception:
                pass
    return None


def main() -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    _ensure_model_loaded()

    if _port_in_use("127.0.0.1", 8000):
        pid = _find_zombie_pid(8000)
        print("\n[start] ❌ Port 8000 ist bereits belegt (API-Zombie).")
        if pid:
            print(f"[start] Blockierender Prozess: PID {pid}")
            print(f"[start] Zum Killen: taskkill /PID {pid} /F")
        print("[start] Oder alle alten Python-Prozesse beenden: Get-Process python | Stop-Process -Force")
        return 1

    if _port_in_use("127.0.0.1", 8501):
        print("[start] ⚠️ Port 8501 belegt — Streamlit wird auf nächsten freien Port ausweichen")

    py = sys.executable

    api = subprocess.Popen(
        [py, os.path.join(HERE, "api.py")],
        cwd=HERE,
        env=env,
    )
    print(f"[start] API launched (pid={api.pid}) → http://localhost:8000/docs")

    time.sleep(1.5)

    streamlit = subprocess.Popen(
        [py, "-m", "streamlit", "run", os.path.join(HERE, "app.py")],
        cwd=HERE,
        env=env,
    )
    print(f"[start] Streamlit launched (pid={streamlit.pid}) → http://localhost:8501")

    def _stop(*_):
        print("\n[start] stopping ...")
        for p in (streamlit, api):
            try:
                p.terminate()
            except Exception:
                pass
        for p in (streamlit, api):
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    try:
        signal.signal(signal.SIGTERM, _stop)
    except Exception:
        pass

    try:
        while True:
            time.sleep(1)
            if api.poll() is not None:
                print(f"[start] API exited with code {api.returncode}")
                _stop()
            if streamlit.poll() is not None:
                print(f"[start] Streamlit exited with code {streamlit.returncode}")
                _stop()
    except KeyboardInterrupt:
        _stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
