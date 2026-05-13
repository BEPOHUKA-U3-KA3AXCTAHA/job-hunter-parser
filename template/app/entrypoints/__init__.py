"""Entrypoints — composition root.

CLI and API live here. These ARE allowed to import directly from
adapter implementations (composition is their job). For everything
else, go through `app.modules.<x>` public surface.
"""
