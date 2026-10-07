"""Event schema contracts shared by producers, SDK and the platform.

Repository-level package: it is intentionally NOT installed into site-packages
because the top-level name ``schemas`` is far too generic and would collide with
unrelated third-party distributions. It is importable in tests via the
``pythonpath`` setting in ``pyproject.toml``.
"""
