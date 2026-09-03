"""Start the local dashboard: http://127.0.0.1:8765"""
import _bootstrap  # noqa: F401

from political_shorts.dashboard import main

if __name__ == "__main__":
    main()
