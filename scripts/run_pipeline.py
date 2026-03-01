from __future__ import annotations

import activity
import diff
import hacktivity
import latest_updates
import normalize


def main() -> None:
    normalize.main()
    diff.main()
    latest_updates.main()
    activity.main()
    hacktivity.main()


if __name__ == "__main__":
    main()
