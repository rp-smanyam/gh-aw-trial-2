#!/usr/bin/env bash

# use path of this example as working directory; enables starting this script from anywhere
cd "$(dirname "$0")"

export PYTHONPATH=../src

if [ "$1" = "prod" ]; then
    echo "Starting Uvicorn server in production mode..."
    # we also use a single worker in production mode so socket.io connections are always handled by the same worker
    # LOG_JSON_FORMAT=true  opentelemetry-instrument --service_name-name agent-leasing uvicorn agent_leasing.server:app --host 0.0.0.0 --workers 1 --log-level info --port 80 --log-config ../src/agent_leasing/util/uvicorn_disable_logging.json
    # LOG_JSON_FORMAT=true  opentelemetry-instrument --service_name-name agent-leasing uvicorn agent_leasing.server:app --host 0.0.0.0 --workers 1 --log-level info --port 80
elif [ "$1" = "dev" ]; then
    echo "Starting Uvicorn server in development mode..."
    # reload implies workers = 1
    # opentelemetry-instrument --service_name agent-leasing uvicorn agent_leasing.server:app --reload --log-level info --port 8000 --log-config ../src/agent_leasing/util/uvicorn_disable_logging.json
    # opentelemetry-instrument --service_name agent-leasing uvicorn agent_leasing.server:app --reload --log-level info --port 8000
else
    echo "Invalid parameter. Use 'prod' or 'dev'."
    exit 1
fi