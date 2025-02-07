"""Creating sensors for upcoming events with filtering by keyword."""

from datetime import datetime, timedelta
import logging

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity, generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MAX_EVENTS, DOMAIN, ICON, CONF_FILTER_KEYWORD

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant, config, add_entities, discovery_info=None
):
    """Set up this integration with config flow."""
    return True


async def async_setup_entry(
    hass: HomeAssistant, config_entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the iCal Sensor from a config entry."""
    config = config_entry.data
    name = config.get(CONF_NAME)
    max_events = config.get(CONF_MAX_EVENTS)
    filter_keyword = config.get(CONF_FILTER_KEYWORD, "")

    # Récupération de l'objet ical_events qui a été stocké dans hass.data
    ical_events = hass.data[DOMAIN][name]
    await ical_events.update()
    if ical_events.calendar is None:
        _LOGGER.error("Unable to fetch iCal")
        return False

    sensors = []
    sensor_name = f"{DOMAIN} {name}"
    for eventnumber in range(max_events):
        sensors.append(
            ICalSensor(
                hass,
                ical_events,
                sensor_name,
                eventnumber,
                filter_keyword,
            )
        )

    async_add_entities(sensors)


class ICalSensor(Entity):
    """Representation of an iCal sensor that shows the Nth upcoming event matching a keyword filter."""

    def __init__(self, hass: HomeAssistant, ical_events, sensor_name, event_number, filter_keyword) -> None:
        """Initialize the sensor.

        Args:
            hass (HomeAssistant): the Home Assistant instance.
            ical_events: The object handling calendar updates.
            sensor_name (str): Name of the sensor/calendar.
            event_number (int): Index of the upcoming event to display.
            filter_keyword (str): Mot clé à utiliser pour filtrer les événements selon leur sommaire.
        """
        super().__init__()
        self._hass = hass
        self.ical_events = ical_events
        self._event_number = event_number
        self._entity_id = generate_entity_id(
            "sensor.{}", f"{sensor_name} event {self._event_number}", hass=self._hass
        )
        self._filter_keyword = filter_keyword.lower() if filter_keyword else ""
        self._event_attributes = {
            "summary": None,
            "description": None,
            "location": None,
            "start": None,
            "end": None,
            "eta": None,
        }
        self._state = None
        self._is_available = None

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the sensor."""
        return f"{self.ical_events.name.lower()}_event_{self._event_number}"

    @property
    def name(self):
        """Return the name of the sensor (dynamically from event summary)."""
        return self._event_attributes["summary"]

    @property
    def icon(self):
        """Return the icon for the frontend."""
        return ICON

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the attributes of the event."""
        return self._event_attributes

    @property
    def available(self):
        """Return True if an event is available."""
        return self._event_attributes["start"] is not None

    async def async_update(self):
        """Fetch new state data for the sensor."""
        _LOGGER.debug("Running ICalSensor async update for %s", self.name)

        await self.ical_events.update()

        event_list = self.ical_events.calendar

        # Appliquer le filtre par mot clé sur le sommaire si défini
        if self._filter_keyword:
            filtered_events = []
            for event in event_list:
                summary = event.get("summary", "").lower()
                if self._filter_keyword in summary:
                    filtered_events.append(event)
            event_list = filtered_events

        if event_list and (self._event_number < len(event_list)):
            val = event_list[self._event_number]
            event_summary = val.get("summary", "Unknown")
            start = val.get("start")

            _LOGGER.debug(
                "Adding event %s - Start %s - End %s - as event %s",
                event_summary,
                val.get("start"),
                val.get("end"),
                str(self._event_number),
            )

            self._event_attributes["summary"] = event_summary
            self._event_attributes["start"] = start.strftime('%Y%m%dT%H%M%S')
            self._event_attributes["end"] = val.get("end").strftime('%Y%m%dT%H%M%S') if val.get("end") else None
            self._event_attributes["location"] = val.get("location", "")
            self._event_attributes["description"] = val.get("description", "")
            self._event_attributes["all_day"] = val.get("all_day")
            # Calcul de l'ETA en jours (ajusté d'un jour)
            self._event_attributes["eta"] = (
                start - datetime.now(start.tzinfo) + timedelta(days=1)
            ).days

            self._state = f"{event_summary} - {start.strftime('%-d %B %Y')}" 
            if not val.get("all_day"): 
                self._state += f" {start.strftime('%H:%M')}"

        else:
            # Aucun événement correspondant n'a été trouvé pour cet index
            self._event_attributes = {
                "summary": None,
                "description": None,
                "location": None,
                "start": None,
                "end": None,
                "eta": None,
            }
            self._state = None
            self._is_available = None
