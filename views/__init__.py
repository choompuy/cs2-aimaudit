"""
The pages: one module per page, each exposing render(ctx).

Importing the submodules here (not lazily) keeps `views.<page>.render`
working after a plain `import views`.
"""

from . import duels, extras, map, overview, shooting, timeline  # noqa: F401
