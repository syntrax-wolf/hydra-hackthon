"""
Horizon Platform — Open Website
Run: python main.py

Opens the website in your browser.
The server must already be running (python service.py).
"""
import os
import sys
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def main():
    port = int(os.getenv("SERVER_PORT", "8501"))
    url = f"http://localhost:{port}"

    # Check if server is running
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=3)
    except Exception:
        print(f"[ERROR] Server is not running at {url}")
        print()
        print("  Start the server first:")
        print("    python service.py")
        print()
        print("  Then in another terminal:")
        print("    python main.py")
        sys.exit(1)

    print(f"Opening {url} in your browser...")
    webbrowser.open(url)
    print("Done!")


if __name__ == "__main__":
    main()
