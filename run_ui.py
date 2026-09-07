#!/usr/bin/env python3
import sys
import threading
import time
import webbrowser
import uvicorn


def open_browser(url: str, delay: float = 1.0):
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"[Warning] Could not automatically open browser: {e}")


def main():
    host = "127.0.0.1"
    port = 8000
    url = f"http://{host}:{port}"

    print("=" * 60)
    print("🎬 Video Search & Highlight Player")
    print(f"   Server running at: {url}")
    print("   Press Ctrl+C to terminate.")
    print("=" * 60)

    # Launch browser after server starts
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    try:
        uvicorn.run("adapters.web.app:app", host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        print("\n[UI Server] Stopped by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
