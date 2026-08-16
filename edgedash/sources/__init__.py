# EdgeDash sources sub-package
#
# Importing each source module here ensures its @register decorator runs,
# populating the SOURCES registry before any agent looks up a source name.
# Add one import per new source file — nothing else needs to change.

from edgedash.sources import arbeitnow as _   # noqa: F401
from edgedash.sources import apify as _apify  # noqa: F401
