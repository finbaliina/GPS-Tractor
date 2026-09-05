import json
from datetime import datetime, timezone
from pathlib import Path


FREE_MONTHLY_REQUESTS = 50_000
USAGE_FILE = Path(".mapbox_usage.json")


def record_mapbox_request() -> dict:
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")

    if USAGE_FILE.exists():
        try:
            usage = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            usage = {}
    else:
        usage = {}

    if usage.get("month") != current_month:
        usage = {"month": current_month, "requests": 0}

    usage["requests"] += 1
    USAGE_FILE.write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")
    return usage


def print_mapbox_usage(usage: dict) -> None:
    requests_used = usage["requests"]
    remaining = max(FREE_MONTHLY_REQUESTS - requests_used, 0)
    percentage = requests_used / FREE_MONTHLY_REQUESTS * 100

    print(
        f"Local Mapbox request count for {usage['month']}: "
        f"{requests_used:,} / {FREE_MONTHLY_REQUESTS:,} requests"
    )
    print(f"Remaining: {remaining:,} ({percentage:.3f}% used)")
    print("This is this project's local estimate; the Mapbox dashboard is authoritative.")
