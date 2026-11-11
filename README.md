# SIT-INF1103-Project-1

## Running the app

1. Create a `.env` file in the repo root with your keys: `OPENROUTER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `FIRECRAWL_API_KEY` (optional) and `SECRET_KEY`. The Postgres settings have working defaults; override `POSTGRES_PASSWORD` etc. here if needed.

2. Start everything (app and Postgres database):
```bash
docker compose up --build
```

3. Open http://localhost:8080.

Use `--build` after every pull so the image includes the latest code. Data is kept in Docker volumes between restarts. `docker compose down` is safe;
`docker compose down -v` also **deletes the database and saved data**.

## How the code is organised

Every layer is a folder; the Python modules of all layers are copied side by side into `/app` in the image.
Design diagrams (Mermaid source and PNGs) are in [`docs/`](docs).

| Manager | Folder | Classes |
|---|---|---|
| IO | `io-layer/` | `IOManager` (HTTP routes, input checks, JSON shaping), `DocumentExtractor` <<abstract>> with `PdfExtractor`, `DocxExtractor`, `TxtExtractor`; the browser UI (`*.html`, `*.js`, `styles.css`, `nginx.conf`) |
| Logic | `logic-layer/` | `AuthService`, `AnalysisService`, `AnalysisComparer`, `AlertPolicy`, `AlertRule` <<abstract>> with `ClauseCountAlertRule`, `Notifier` / `ApprovalChannel` interfaces with `TelegramNotifier`; `app.py` wires everything together (`Application`) |
| AI | `ai-layer/` | `AIManager` (prompts, model failover, response validation), `RecoverySession`, `FirecrawlResearcher` |
| Data | `data-layer/` | `DataManager`, `Database`, `Repository` <<abstract>> with `UserRepository`, `ReferenceRepository`, `AnalysisRepository`, and the entities `User`, `Analysis`, `Clause`, `ClauseReference`, `LegalReference` |

The app creates its database tables from the entity classes (`data-layer/entities.py`) when it starts. Tables that
already exist are not changed, so after changing an entity, delete the old database with `docker compose down -v`.

## Telegram notifications

- When **every** AI model fails during an analysis, the engineering group chat receives an alert with
  **Fix** and **Stop** buttons. Fix retries the analysis with the backup model; Stop cancels it (the user sees
  "AI service unavailable"). The alert is sent at most once per analysis.
- A completed analysis with 7 or 8 flagged clauses notifies the "medium" chat; more than 8 notifies the "high" chat.
  Cached results do not notify again.
- Chats are configured with `TELEGRAM_GROUP_CHAT_ID`, `ALERT_MEDIUM_CHAT_ID` and `ALERT_HIGH_CHAT_ID`;
  the defaults are the existing chats.
- If Telegram returns HTTP 401, check `TELEGRAM_BOT_TOKEN` and recreate the container with the corrected
  environment; restarting an existing container does not update its environment. HTTP 400 or 403 can indicate
  a bad chat ID, missing bot membership, or an individual who has not started the bot.

## Running the tests

The tests never call a live AI or Telegram. The report lists every test case with its input, expected result,
actual result and `test case N pass`, then a summary. They run in GitHub Actions on every push
([`.github/workflows/tests.yml`](.github/workflows/tests.yml)), against a PostgreSQL service container.

To run them yourself, point `TEST_DATABASE_URL` at a throwaway PostgreSQL database (every test empties its tables)
and run `python tests/run_report.py`.

| File | What it checks |
|---|---|
| `tests/test_api_behaviour.py` | All 13 HTTP endpoints: auth, analysis, caching, history, compare, admin, health, Telegram thresholds |
| `tests/test_ai_notifications.py` | Model failover, Fix / Stop recovery, error categories, response validation |
| `tests/test_oop_design.py` | Abstract classes, inheritance, overriding, interfaces, polymorphism, encapsulation, aggregation |
| `tests/test_data_layer.py` | Repositories, entities, composition (cascade delete), table creation |

Without `TEST_DATABASE_URL` the tests are skipped. They never use `DATABASE_URL`.
