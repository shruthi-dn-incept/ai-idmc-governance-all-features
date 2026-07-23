#!/bin/bash
# Entrypoint for the UI Docker container.
# Starts all six MCP servers, waits for them, then runs the UI in foreground.
set -e

# Guard: this script OVERWRITES the repo-root .env from process env vars (so
# secrets stay out of the image). On a dev machine that destroys your local
# credentials. For local runs start the processes directly instead.
if [ ! -f /.dockerenv ] && [ -z "$RUNNING_IN_CONTAINER" ]; then
  echo "REFUSING to run outside a container: this script overwrites .env."
  echo "For local dev, run the servers and UI directly, e.g.:"
  echo "  python -m idmc_governance.servers.governance_engine   # :8765"
  echo "  python -m idmc_governance.servers.lineage_reporter    # :8766"
  echo "  python -m idmc_governance.servers.glossary_manager    # :8767"
  echo "  python -m idmc_governance.servers.dq_monitor          # :8768"
  echo "  python -m idmc_governance.servers.data_onboarding     # :8769"
  echo "  python -m idmc_governance.servers.ai_governance       # :8770"
  echo "  python -m idmc_governance.ui.app                      # :8080"
  exit 1
fi

export IDMC_FRS_HOST="${IDMC_FRS_HOST:-dmp-us.informaticacloud.com}"
export IDMC_DQ_HOST="${IDMC_DQ_HOST:-usw1-dqcloud.dmp-us.informaticacloud.com}"
export IDMC_IDENTITY_HOST="${IDMC_IDENTITY_HOST:-dmp-us.informaticacloud.com}"
export CDGC_API_BASE="${CDGC_API_BASE:-https://cdgc-api.dmp-us.informaticacloud.com}"
export AI_GOVERNANCE_URL="http://127.0.0.1:9770/mcp"
export GOVERNANCE_ENGINE_URL="http://127.0.0.1:9765/mcp"
export LINEAGE_REPORTER_URL="http://127.0.0.1:9766/mcp"
export GLOSSARY_MANAGER_URL="http://127.0.0.1:9767/mcp"
export DQ_MONITOR_URL="http://127.0.0.1:9768/mcp"
export DATA_ONBOARDING_URL="http://127.0.0.1:9769/mcp"
export GOVERNANCE_MCP_PORT=9765
export LINEAGE_MCP_PORT=9766
export GLOSSARY_MCP_PORT=9767
export DQ_MONITOR_MCP_PORT=9768
export DATA_ONBOARDING_MCP_PORT=9769
export AI_GOVERNANCE_MCP_PORT=9770
export GOVERNANCE_UI_PORT=9080
export GOVERNANCE_UI_HOST=0.0.0.0

# Servers read creds from a repo-root .env (not os.environ). Materialize it from
# the injected env vars so secrets stay OUT of the image.
env | grep -E '^(IDMC_|CDGC_|CDQ_|ANTHROPIC_|SNOWFLAKE_)' > .env
echo "=== materialized .env with $(wc -l < .env) keys ==="

echo "=== Starting governance_engine on :9765 ==="
python -m idmc_governance.servers.governance_engine &

echo "=== Starting lineage_reporter on :9766 ==="
python -m idmc_governance.servers.lineage_reporter &

echo "=== Starting glossary_manager on :9767 ==="
python -m idmc_governance.servers.glossary_manager &

echo "=== Starting dq_monitor on :9768 ==="
python -m idmc_governance.servers.dq_monitor &

echo "=== Starting data_onboarding on :9769 ==="
python -m idmc_governance.servers.data_onboarding &

echo "=== Starting ai_governance on :9770 ==="
python -m idmc_governance.servers.ai_governance &

echo "=== Waiting for MCP servers ==="
for PORT in 9765 9766 9767 9768 9769 9770; do
  for i in $(seq 1 30); do
    if python -c "import socket; s=socket.create_connection(('127.0.0.1',$PORT),1); s.close()" 2>/dev/null; then
      echo "  :$PORT ready"
      break
    fi
    echo "  Waiting for :$PORT (attempt $i)..."
    sleep 2
  done
done

echo "=== Starting UI on :9080 ==="
exec python -m idmc_governance.ui.app
