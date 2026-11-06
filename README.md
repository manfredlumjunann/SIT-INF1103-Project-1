# SIT-INF1103-Project-1

## AI error notifications

The Docker image runs the logic layer and imports the shared OpenRouter code from
[`ai-layer/ai_service.py`](ai-layer/ai_service.py). Configure the primary and
fallback model names in that file, then rebuild the image. Both the logic layer
and the standalone AI layer use this list; the primary matches the logic layer's
previous default.

Set `OPENROUTER_API_KEY` and `TELEGRAM_BOT_TOKEN` in the container environment.
The existing AI error notification sends to the configured group in the logic
layer. Alerts include the failed model, error category and HTTP status where
available, but not API keys, contract text, or provider response bodies.
Telegram delivery errors are logged without interrupting analysis.

Completed analyses with 7 or 8 flagged clauses notify the existing medium-count
individual chat; more than 8 notify the existing high-count individual chat.
Cached results do not send another clause notification. These recipients are
defined in [`logic-layer/app.py`](logic-layer/app.py), independently of the AI
error group.

If Telegram returns HTTP 401, check `TELEGRAM_BOT_TOKEN` and recreate the
container with the corrected environment; restarting an existing container does
not update its environment. HTTP 400 or 403 can indicate a bad chat ID, missing
bot membership, or an individual who has not started the bot. Delivery failures
are recorded in `/var/log/supervisor/gunicorn.err.log` inside the container.

Alerts are generated when an analysis request encounters:

- A missing or failed primary model, even if a fallback succeeds.
- No configured models or failure of every configured model, with the category
  for each failed model. An empty fallback list alone does not trigger an alert.
- Missing OpenRouter credentials, malformed AI output, document extraction
  errors, or a failure in optional Firecrawl research or workaround enhancement.
  Optional research and enhancement failures are reported but analysis continues.

The model check happens during an analysis request, not when Docker builds or
starts. A previously saved analysis may be returned from cache without calling
the AI; use a different contract or context to exercise failover. If Firecrawl
is not configured, its optional research is skipped.

Run the integration tests with the Python dependencies installed:

```powershell
python -m unittest discover -s tests -v
```