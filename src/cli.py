"""DEPRECATED — moved to app.entrypoints.cli.main"""
from app.entrypoints.cli.main import *  # noqa: F401, F403
from app.entrypoints.cli.main import app  # noqa: F401

if __name__ == "__main__":
    app()
