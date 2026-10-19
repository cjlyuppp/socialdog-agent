"""Single worker on an unpublished container network behind the HTTPS gateway."""
import os
from pathlib import Path

import uvicorn

from socialdog_agent.server.app import ServerConfig, create_app


if __name__ == "__main__":
    app = create_app(ServerConfig(
        database=Path("/workspace/workspace.sqlite"),
        state_dir=Path("/workspace/audit"),
        public_origin=os.environ["SOCIALDOG_PUBLIC_ORIGIN"],
        history_package=Path("/data/history_202303_asof_0315"),
        reference_db=Path("/data/history_202303_reference/reference.sqlite"),
        static_dir=Path("/app/frontend/dist"),
    ))
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1,
                proxy_headers=False, access_log=False, timeout_graceful_shutdown=150)
