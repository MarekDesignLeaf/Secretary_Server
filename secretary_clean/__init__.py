"""Clean Secretary backend foundation.

This package is intentionally separate from the legacy application entrypoint.
It uses the existing repository only as source material while defining a clean
backend-first product architecture and API contract.
"""

__all__ = ["create_app"]

from .app import create_app
