# League Lens

Read-only League of Legends champion research dashboard built with Streamlit and PostgreSQL.

This release branch contains only the deployable dashboard. Coursework and ingestion remain on their existing branches and in the local working project.

## Run locally

Use Python 3.12, install requirements.txt, and set APP_DATABASE_URL privately to the existing app_reader connection. Then run:

    python -m streamlit run app/streamlit_app.py

## Deploy

Use Render New > Blueprint with this branch and render.yaml. The Blueprint uses a free web service and prompts for APP_DATABASE_URL. See [deployment instructions](docs/deployment.md) and [dashboard behavior](docs/streamlit_app.md).

## Verify

    python -m unittest discover -s tests -p test_streamlit_app.py

Data and champion artwork: Riot Games API / Data Dragon. Independent educational fan project, not endorsed by Riot Games.
