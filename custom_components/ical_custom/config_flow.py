"""Config flow for ical_custom integration."""
import logging

import voluptuous as vol

from homeassistant import config_entries, core, exceptions
from homeassistant.const import CONF_NAME, CONF_URL, CONF_VERIFY_SSL
import homeassistant.helpers.config_validation as cv

from .const import CONF_DAYS, CONF_MAX_EVENTS, DOMAIN, CONF_FILTER_KEYWORD

DEFAULT_MAX_EVENTS = 5
DEFAULT_DAYS = 365
DEFAULT_FILTER_KEYWORD = ""  # Par défaut, aucun filtre n'est appliqué

_LOGGER = logging.getLogger(__name__)

# Schéma de configuration mis à jour pour inclure le champ de filtre par mot-clé
DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_URL): cv.string,
        vol.Optional(CONF_MAX_EVENTS, default=DEFAULT_MAX_EVENTS): cv.positive_int,
        vol.Optional(CONF_DAYS, default=DEFAULT_DAYS): cv.positive_int,
        vol.Optional(CONF_VERIFY_SSL, default=True): cv.boolean,
        vol.Optional(CONF_FILTER_KEYWORD, default=DEFAULT_FILTER_KEYWORD): cv.string,
    }
)


class PlaceholderHub:
    """Placeholder class to make tests pass.

    TODO: Remplacer cette classe par la logique d'authentification réelle de votre package.
    """

    def __init__(self, host):
        """Initialize."""
        self.host = host

    async def authenticate(self, username, password) -> bool:
        """Test if we can authenticate with the host."""
        return True


async def validate_input(hass: core.HomeAssistant, data):
    """Validate the user input allows us to connect.

    'data' contient les clés définies dans DATA_SCHEMA.
    """
    # TODO: Valider que les données permettent de se connecter à la source réelle.
    hub = PlaceholderHub(data["name"])

    if not await hub.authenticate(data["url"], data["url"]):
        raise InvalidAuth

    # Retourner les informations à stocker dans le config entry.
    return {"title": data[CONF_NAME], "url": data[CONF_URL]}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ical_custom."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_UNKNOWN

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                return self.async_create_entry(title=info["title"], data=user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""
