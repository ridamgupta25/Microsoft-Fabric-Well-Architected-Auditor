"""Check library. Importing this package registers all checks."""
# Importing the modules runs the @workspace_check / @pipeline_check decorators,
# which append each check to the registries in base.py.
from . import workspace_checks  # noqa: F401
from . import pipeline_checks  # noqa: F401
