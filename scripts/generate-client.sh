#! /usr/bin/env bash

set -e
set -x

cd backend
# write the file from inside Python: importing app.main logs to stdout
# (structlog) and a plain `>` redirect would smear it into the JSON
FASTAPI_ENV=development uv run python -c "import app.main, json; open('../openapi.json', 'w').write(json.dumps(app.main.app.openapi()))"
cd ..
mv openapi.json frontend/
bun run --filter frontend generate-client
bun run lint
