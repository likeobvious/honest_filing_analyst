# Frontend deployment

`src/ui/` is the maintained frontend source. The FastAPI app serves static files from `FRONTEND_DIR`, which defaults to `frontend/`; Docker sets `FRONTEND_DIR=/app/frontend` and the existing Dockerfile copies the repository's `frontend/` directory into that location. FastAPI includes the API router before mounting static files at `/`, so the static mount does not replace the registered API routes.

The Docker build context currently has no `frontend/` directory. Do not copy or edit the UI manually. Stage it from its canonical source before the existing Compose build:

```powershell
npm ci --prefix .\src\ui
python .\src\ui\prepare_docker_frontend.py
docker compose build honest-analyst-app
python .\src\ui\prepare_docker_frontend.py --clean
docker compose up --no-build -d
```

The staging helper produces the Docker-expected `frontend/` output, excludes `node_modules` from the source copy, and copies only the pinned PDF.js browser runtime, worker, CMaps, standard fonts, and WASM assets needed at runtime. It refuses to overwrite an existing `frontend/` unless that directory has the helper's generated-output marker. Re-running the helper replaces only an output it previously generated.

After a successful image build, `--clean` removes only the helper-generated staging directory, avoiding a duplicate frontend tree in the repository. If the build fails, leave the staging output in place while diagnosing or rerunning the build; clean it afterward.

The helper changes no backend or Docker files. To automate staging through `make up`, or to make Docker build directly from `src/ui/`, obtain Teammate 1's approval to change the relevant build integration. The current Makefile does not invoke the helper automatically.

After startup, verify the existing routes remain available:

```powershell
Invoke-WebRequest http://localhost/api/v1/health
Invoke-WebRequest http://localhost/
```

To verify a citation PDF, use its actual backend URL, for example `http://localhost/pdfs/<filename>.pdf#page=1`. Do not use the synthetic PDFs in `test-fixtures` as filing evidence.
