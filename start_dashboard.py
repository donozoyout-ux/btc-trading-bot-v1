"""Cloud deployment entrypoint for the read-only BTC dashboard."""

import os
import sys

from dashboard_server import main


if __name__ == "__main__":
    host = "0.0.0.0"
    port = os.environ.get("PORT", "8080")
    sys.argv = ["dashboard_server.py", "--host", host, "--port", str(port)]
    main()
