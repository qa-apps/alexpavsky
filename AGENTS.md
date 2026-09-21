## Deployment — GitHub and the VPS move together

**Every change to alexpavsky.com ships to both, in the same act. Never one without the other.**

The live service is `/var/www/alexpavsky.com/html` on `alexpavsky-prod`. It is **not**
a git checkout: `ops/deploy-backend.sh` rsyncs a working directory to it. So a deploy
does not touch GitHub on its own, and GitHub does not reach the VPS on its own.

That is exactly how this project broke. Edits were deployed straight to the server and
never pushed, and the two drifted for months: 15 functions — the httpOnly cookie auth,
the free-model pool and curator, Langfuse tracing — lived only in production, while the
repository still carried a dead Gemini image path. The visible cost was a frontend that
sent `Authorization: Bearer undefined` on every authenticated request from August until
2026-09-21, because the repo had no record that the backend had stopped returning a
token.

### The order

1. Commit the change.
2. Push it to GitHub.
3. `./ops/deploy-backend.sh`

The script enforces this: it refuses to run when the working tree is dirty, when the
branch has no upstream, or when `HEAD` differs from `origin/<branch>`. It is not a
reminder — the deploy will not proceed.

For a genuine emergency, `ALLOW_DIRTY_DEPLOY=1 ./ops/deploy-backend.sh` overrides it and
says so loudly. **Push immediately afterwards**, or the drift starts again.

### Also true

- `ops/deploy-backend.sh` rsyncs with `--delete`. A file missing from this repository is
  **deleted from the server**. Before deploying, make sure everything production needs is
  committed here — `langfuse_tracer.py` and `ops/` were once missing and would have been
  destroyed by a deploy from a clean clone.
- Secrets are never committed. The script builds the remote env file from your local
  `.env`, which is gitignored.
- After deploying, verify against the public edge, not the server: `/`, `/api/health`,
  and a real `POST /api/chat`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
