from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

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
