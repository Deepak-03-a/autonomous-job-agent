"""Deployment entrypoint; the legacy app module remains import-compatible."""

from app.main import app

__all__ = ["app"]
