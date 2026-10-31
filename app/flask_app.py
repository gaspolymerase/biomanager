"""The Flask application object, on its own.

app.py builds the application: its configuration, its error handlers, what
every page is given to render with. The pages themselves live in modules
beside it (app/colony_routes.py, app/zebrafish_routes.py and the rest), and
those modules need the object to hang a route on. Keeping it here is what
lets them have it without importing app.py, which imports them.
"""
from __future__ import annotations

from flask import Flask

app = Flask(__name__)
