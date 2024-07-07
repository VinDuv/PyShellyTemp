"""
Logging configuration
"""

import logging
import os
import sys

LOGGER = logging.getLogger(__name__)


def configure_logging() -> None:
    """
    Configure the logging system according to the LOG_DEBUG environment
    variable:
    - If set to '1', 'true', 'yes', 'y', the main logger will be configured at
      DEBUG level.
    - If set to '0', 'false', 'no', 'n', '', or not set, the main logger will be
      configured at INFO level.
    - If set to a comma-separated list of modules/packages, the main logger will
      be configured at INFO level and the loggers for the specified modules will
      be set to DEBUG level. If a package is set to DEBUG, all modules in that
      package will be affected.
    """

    value = os.environ.get('LOG_DEBUG', '')
    value_lower = value.lower()

    if value_lower in {'1', 'true', 'yes', 'y'}:
        logging.basicConfig(level=logging.DEBUG)
        return

    logging.basicConfig(level=logging.INFO)

    if value_lower in {'0', 'false', 'no', 'n', ''}:
        return

    for mod_name in value.split(','):
        mod_name = mod_name.strip()
        if mod_name not in sys.modules:
            alt_mod_name = f'pyshellytemp.{mod_name}'
            if alt_mod_name in sys.modules:
                mod_name = alt_mod_name
            else:
                LOGGER.warning("Module %r is not registered, setting its log "
                "level to DEBUG anyway", mod_name)

        logging.getLogger(mod_name).setLevel(logging.DEBUG)
