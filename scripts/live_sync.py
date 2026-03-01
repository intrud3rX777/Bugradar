from __future__ import annotations

import os
import time
import traceback

import run_pipeline


def main() -> None:
    interval = int(os.getenv("BUG_RADAR_REFRESH_SECONDS", "1800"))
    interval = max(60, interval)

    print(f"[live-sync] started with refresh interval: {interval}s")

    while True:
        started = time.time()
        try:
            run_pipeline.main()
            elapsed = time.time() - started
            print(f"[live-sync] refresh complete in {elapsed:.1f}s")
        except Exception:
            print("[live-sync] refresh failed:")
            traceback.print_exc()
            elapsed = time.time() - started

        sleep_for = max(5, interval - int(elapsed))
        print(f"[live-sync] sleeping for {sleep_for}s")
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
