# FINDIT

## Lost & Found Matcher

A modern platform that helps people report lost/found belongings and intelligently identify potential matches.

## Overview

FINDIT is a Flask web application for reporting lost and found belongings, browsing and filtering reports, and reviewing possible matches between them. People can create an account, submit a report with an optional image, and manage their reports and match notifications.

The project stores its application data in a local SQLite database. The database and uploaded images are created at runtime and are intentionally excluded from Git.

## Features

- Create an account, sign in, and manage a personal dashboard.
- Submit lost or found reports with a title, category, description, location, event date, and optional image.
- Browse, search, and filter reports by type, category, location, date, and text.
- Generate potential matches for open reports and display the score components and reasons.
- Review match details and update match status.
- Receive notifications when a new potential match is found.
- Keep contact details off public item pages and use sign-in-gated match notifications for contact.
- Use a responsive premium UI across screen sizes.
- Use JSON endpoints for item, match, dashboard, and profile data.

## Smart Matching

The matching engine is a deterministic, rule-based comparison implemented in `database/matching.py`. It does not call an AI model or an external matching service.

It compares open reports of opposite types (lost against found), and skips reports submitted by the same account. The overall score is a weighted combination of six signals:

| Signal | Weight |
| --- | ---: |
| Category similarity | 25% |
| Item-name similarity | 20% |
| Description similarity | 20% |
| Location text similarity | 15% |
| Event-date proximity | 10% |
| Shared attributes | 10% |

Text similarity normalizes case and Unicode, removes common stop words, applies limited plural and synonym normalization, and compares tokens and phrases using Python's `difflib.SequenceMatcher`. The attribute signal looks for overlapping terms in known color, material, feature, and brand vocabularies. Date similarity declines linearly with the number of days between ISO-format dates and reaches zero at 30 days. Location matching compares the entered location text; it does not calculate geographic distance.

Scores are rounded to whole numbers. Results below 50 are not shown as potential matches, and reports with different non-empty categories are capped below that threshold. Display labels are Possible Match (50–69), Likely Match (70–84), and Strong Match (85–100). The score is a ranking heuristic, not a probability or a guarantee that two reports refer to the same item. Results include the component scores and textual reasons so people can review them themselves.

## Tech Stack

- Python
- Flask
- SQLite
- Werkzeug (included as a Flask dependency; used for password hashing and uploaded-file handling)
- Server-rendered HTML templates with Jinja
- HTML, CSS, vanilla JavaScript, and SVG assets
- pytest (development/test dependency; tests use Python's `unittest` assertions)
- Gunicorn (production WSGI server)

## Project Structure

```text
FINDIT/
├── app.py                  # Flask application, routes, and JSON endpoints
├── config.py               # Development, production, and testing configuration
├── requirements.txt        # Production Python dependencies
├── requirements-dev.txt    # Test/development dependencies
├── database/
│   ├── database.py         # SQLite connection and query helpers
│   ├── matching.py        # Deterministic report-matching algorithm
│   └── schema.py           # SQLite table and index definitions
├── static/
│   ├── css/                # Application styles
│   ├── images/             # SVG brand artwork; uploads are runtime data
│   └── js/                 # Browser-side interactions
├── templates/
│   ├── base.html           # Shared page layout
│   ├── index.html          # Home page
│   └── page.html           # Shared application pages
├── tests/
│   ├── test_app.py         # Application and report-flow tests
│   └── test_matching.py    # Matching algorithm tests
├── instance/               # Runtime SQLite database (Git-ignored)
├── .gitignore
└── README.md
```

## Installation

Python and `pip` are required. From the project directory, create and activate a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

On macOS or Linux, activate the environment with:

```sh
source .venv/bin/activate
```

`requirements.txt` lists the production dependencies. `requirements-dev.txt` includes those dependencies and pytest for running the test suite.

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Flask session-signing key. Set a unique, randomly generated value and keep it private, especially outside local development. |

For example, in PowerShell, generate a random value and set it for the current terminal session:

```powershell
$env:SECRET_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
```

An `.env.example` file provides a placeholder only. Replace it with a randomly generated secret in your local environment; do not commit a real `.env` file. FINDIT reads `SECRET_KEY` from the process environment and exits with a clear configuration error if it is missing. The SQLite database path and upload directory are configured in `config.py`; they are not currently environment-variable settings.

## Running Locally

With the virtual environment activated, set `SECRET_KEY` as above, then start Flask's local development server:

```powershell
python -m flask --app app:create_app run --debug
```

Open the local URL printed by Flask (usually `http://127.0.0.1:5000`). On first start, the application initializes its database under `instance/` and creates the configured image-upload directory under `static/images/uploads/`. Both locations contain local runtime data and are Git-ignored.

The Flask development server and debug mode are for local development only; use a production WSGI server and deployment-specific configuration when hosting the application.

## Testing

Run the test suite with pytest:

```powershell
python -m pytest -q
```

The tests cover report flows, account behavior, API interactions, uploads, and matching scores/reasons. Test runs use temporary databases and upload directories.

## Deploying on Render

Set `SECRET_KEY` as a private environment variable in the Render service settings. Use these commands:

- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `gunicorn --bind 0.0.0.0:$PORT app:app`

The application currently uses a local SQLite database and local image storage. These are not durable across Render deployments or instances; configure persistent or managed storage before relying on production user data.

## Screenshots

No screenshots are currently included. Add screenshots of the application here before publication, making sure they contain no real account information, report data, or uploaded personal images.

## Security & Privacy

- Account passwords are stored as Werkzeug password hashes; they are not stored as plain text.
- Form and API mutations use session-based CSRF tokens. Session cookies are configured as HTTP-only and `SameSite=Lax`; the `Secure` cookie flag is enabled outside debug and test configurations.
- Uploaded images are restricted by extension and file signature, assigned generated filenames, and subject to a 16 MB request-size limit.
- Report details and uploaded report images are used to support browsing and matching. Avoid putting contact details, credentials, or other sensitive personal information in report text or images.
- Keep `.env` files, local databases, virtual environments, caches, and uploaded user files out of version control. Never commit real credentials, keys, or private user data.
- The application refuses to start without an environment-provided secret key. The built-in server and debug mode are not intended for public production use. Review deployment settings and perform a security review before hosting real user data.

## Future Improvements

- Configure durable database and upload storage for production, including managed storage options.
- Add moderation and clearer privacy controls for reports and images.
- Evaluate and tune the matching heuristic using representative, privacy-safe test data.
- Expand claim and handoff workflows and improve accessibility and localization.

## License

No license has been selected or included yet. Add a `LICENSE` file before publication if you intend to grant reuse rights; without one, do not assume that the project may be reused or redistributed.
