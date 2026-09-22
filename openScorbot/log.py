"""Legacy logging hook. Applications configure their own handlers."""

import logging

logger = logging.getLogger("scorbot.legacy")
logger.addHandler(logging.NullHandler())
