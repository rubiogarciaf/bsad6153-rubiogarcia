# Publish League Lens on GitHub and Render

GitHub stores the source and Render runs the Python/Streamlit web service.
The GitHub Student Developer Pack provides account benefits. This deployment
uses Render's free web-service plan and the existing Supabase database.

## Release preparation

1. Confirm the GitHub owner and repository. The current origin is
   `rubiogarciaf/bsad6153-rubiogarcia`; the local branch is `milestone-3`.
2. Review pending changes before committing. Publish the intended dashboard,
   `db/queries.sql`, `.streamlit/config.toml`, dependencies, `render.yaml`, and
   their documentation/tests together. Keep the surrounding course folder,
   `.venv`, `.env`, and `.streamlit/secrets.toml` out of the release.
3. Run the existing reader-boundary checks:

   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_streamlit_app.py
   ```

4. Push the reviewed release to GitHub. Record the actual branch and commit;
   select that branch when creating the Render Blueprint. The Blueprint follows
   its selected branch because `render.yaml` does not hard-code one.

## Render setup

1. Sign in to Render and authorize its GitHub connection for the chosen repository.
2. Choose **New > Blueprint**, select the repository and release branch, and
   use `render.yaml` at the repository root. Leave Root Directory empty for this
   repository: `app/` and `requirements.txt` are already at the repository root.
3. Confirm the `league-lens` service uses the **Free** instance type. The file
   pins Python 3.12.14, installs `requirements.txt`, and starts Streamlit on
   Render's supplied `$PORT` at `0.0.0.0`.
4. Enter `APP_DATABASE_URL` privately in Render when prompted. Use the existing
   read-only `app_reader` connection, including its pooler suffix if applicable.
   The owner/ingestion `DATABASE_URL`, Riot API key, and AI keys are not needed
   by this dashboard. Never paste credentials into chat.
5. Deploy and wait for Render to report **Live**. Open the actual HTTPS service
   URL returned by Render; its name may differ if the preferred name is taken.

The database endpoint must be reachable from Render. If the Supabase direct
endpoint is unreachable, use the project's compatible pooler connection for
`app_reader`, preserving the correct project suffix, password encoding, and TLS.

## Verify the published app

- Visit `/_stcore/health`; it should return `ok`. This checks the Streamlit
  process, not database availability.
- Verify that snapshots and data load, then try all five views, filters, CSV
  download, and Refresh data. A friendly unavailable message means the process
  is running but the database/configuration still needs work.
- Confirm no credentials or detailed Python exceptions appear in the interface.
- Check the deployed commit matches the intended GitHub release. Future pushes
  to the linked branch trigger deployments; keep unfinished work on other branches.

Free Render web services can sleep after inactivity and take time to wake up.
Daily Riot collection remains a separate GitHub Actions workflow; deploying the
dashboard does not start collection or configure its secrets. Scheduled Actions
use the repository's default branch, which may differ from the web service branch.

## Current handoff status

The dashboard-only release is published on GitHub at
[`release/league-lens-2026-11-24`](https://github.com/rubiogarciaf/bsad6153-rubiogarcia/tree/release/league-lens-2026-11-24),
initial application commit `2f99608b7912bb8a180af740906acbbf13579152`.
The working branch and its uncommitted coursework changes were preserved.

Render service `league-lens` was created in My Workspace using the Free plan in
Ohio. [Render dashboard](https://dashboard.render.com/web/srv-dc31df5g1s2s73bgevgg).
Its assigned URL is [League Lens](https://league-lens.onrender.com).
Render reported the deployment with database access **Live** on November 24,
2026. The existing read-only connection was stored in Render's private environment
settings with explicit authorization. The public dashboard was verified in a
browser: the November 24 snapshot, patch 16.23, charts, and 20 Most picked rows
loaded successfully. The public health endpoint returned HTTP 200 with `ok`.
The service was created directly through Render's service API; `render.yaml`
provides the equivalent Blueprint configuration for future setup. The running
service currently uses Render's default process health check.

Local verification: seven reader-boundary tests passed; all five Streamlit
views completed without errors against the configured read-only database;
the local `/_stcore/health` endpoint returned HTTP 200 with `ok`.

Official references: [Render Blueprints](https://render.com/docs/blueprint-spec),
[Python versions](https://render.com/docs/python-version),
[free service limits](https://render.com/docs/free), and
[GitHub Student Developer Pack](https://education.github.com/pack).
