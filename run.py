"""Development entry point for NutriMind AI.

Usage::

    python run.py            # http://127.0.0.1:5000

Applies any pending database migrations first, so ``git clone && python run.py``
still works with no setup. That convenience is deliberately confined to this
file: it runs only under ``__main__``, so importing ``nutrimind:create_app`` from
a WSGI server never triggers it. Production applies migrations as an explicit
deployment step (``flask db upgrade``) because several workers starting at once
must not race each other to migrate the same database.

Production runs should use a WSGI server, e.g.
``waitress-serve --call nutrimind:create_app`` (see docs/DEPLOYMENT.md).
"""

from nutrimind import apply_migrations, create_app

app = create_app()

if __name__ == "__main__":
    apply_migrations(app)
    app.run(host="127.0.0.1", port=5000, debug=app.config["DEBUG"])
