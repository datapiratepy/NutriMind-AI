"""Download the third-party UI assets and serve them from this application.

    python scripts/vendor_cdn_assets.py

Why this exists
---------------
``base.html`` loads Bootstrap, Bootstrap Icons, ``marked`` and **DOMPurify**
from ``cdn.jsdelivr.net``. DOMPurify is the only thing standing between
model-rendered markdown and script execution, and it arrives over the network
from a third party on every page load. Whoever controls that response controls
the sanitiser.

Subresource Integrity would pin the bytes. Vendoring removes the question
entirely — no third-party origin in the CSP, nothing to pin, and the app keeps
working if jsdelivr is unreachable. That is why this script downloads rather
than emitting ``integrity=`` attributes.

Once ``nutrimind/static/vendor/`` exists, two things change automatically:

* ``base.html`` serves the local copies;
* the CSP in ``nutrimind/security.py`` drops every external origin, tightening
  ``script-src`` and ``style-src`` to ``'self'``.

Re-run after changing a version below. Delete the directory to go back to the
CDN. The hashes printed at the end are recorded in ``vendor/MANIFEST.txt`` so a
later run can tell whether upstream bytes changed under a fixed version.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "nutrimind" / "static"
VENDOR = STATIC / "vendor"

#: (local name, url). Versions match what base.html referenced when vendored.
ASSETS = [
    ("bootstrap.min.css",
     "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"),
    ("bootstrap.bundle.min.js",
     "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"),
    ("bootstrap-icons.min.css",
     "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css"),
    ("marked.min.js",
     "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"),
    ("purify.min.js",
     "https://cdn.jsdelivr.net/npm/dompurify@3.1.5/dist/purify.min.js"),
]

#: bootstrap-icons.min.css references its font files relatively.
FONTS = [
    ("fonts/bootstrap-icons.woff2",
     "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff2"),
    ("fonts/bootstrap-icons.woff",
     "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff"),
]


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "nutrimind-vendor"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        return response.read()


def main() -> int:
    VENDOR.mkdir(parents=True, exist_ok=True)
    (VENDOR / "fonts").mkdir(exist_ok=True)

    manifest = []
    for name, url in ASSETS + FONTS:
        try:
            body = _fetch(url)
        except Exception as exc:  # noqa: BLE001 — report and stop, do not half-vendor
            print(f"FAILED {name}: {exc}", file=sys.stderr)
            print("Nothing was changed. The application keeps using the CDN.",
                  file=sys.stderr)
            return 1
        target = VENDOR / name
        target.write_bytes(body)
        digest = hashlib.sha384(body).hexdigest()
        manifest.append(f"{name}  sha384:{digest}  {len(body):,}B  {url}")
        print(f"  {name:26s} {len(body):>9,} B  sha384:{digest[:16]}…")

    (VENDOR / "MANIFEST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    print(f"\nVendored {len(manifest)} files into {VENDOR}")
    print("base.html now serves them locally and the CSP no longer allows any "
          "external origin. Restart the application.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
