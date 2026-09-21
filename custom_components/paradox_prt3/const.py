"""Constants for the Paradox PRT3 integration."""

DOMAIN = "paradox_prt3"

CONF_PORT = "port"
CONF_BAUDRATE = "baudrate"
CONF_CODEC = "codec"
CONF_AREAS = "areas"
CONF_ZONES = "zones"
CONF_ENABLED_AREAS = "enabled_areas"
CONF_ENABLED_ZONES = "enabled_zones"

BAUDRATES = [2400, 9600, 19200, 57600]
DEFAULT_BAUDRATE = 57600

# Events are pushed by the panel; the poll only corrects drift and missed frames.
POLL_INTERVAL_SECONDS = 300
