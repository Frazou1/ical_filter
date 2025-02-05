"""Support for iCal-URLs with keyword filtering."""

import copy
import logging

from homeassistant.components.calendar import (
    ENTITY_ID_FORMAT,
    CalendarEntity,
    CalendarEvent,
    extract_offset,
    is_offset_reached,
)
from homeassistant.const import CONF_NAME
from homeassistant.helpers.entity import generate_entity_id

from .const import DOMAIN, CONF_FILTER_KEYWORD

_LOGGER = logging.getLogger(__name__)

OFFSET = "!!"


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the iCal Calendar platform."""
    config = config_entry.data
    _LOGGER.debug("Running setup_platform for calendar")
    _LOGGER.debug(f"Config: {config}")
    name = config.get(CONF_NAME)
    filter_keyword = config.get(CONF_FILTER_KEYWORD, "")

    entity_id = generate_entity_id(ENTITY_ID_FORMAT, f"{DOMAIN} {name}", hass=hass)

    ical_events = hass.data[DOMAIN][name]

    calendar = ICalCalendarEventDevice(hass, name, entity_id, ical_events, filter_keyword)
    async_add_entities([calendar], True)


class ICalCalendarEventDevice(CalendarEntity):
    """A device for getting the next calendar event from an iCal URL, with optional keyword filtering."""

    def __init__(self, hass, name, entity_id, ical_events, filter_keyword):
        """Create the iCal Calendar Event Device.

        Args:
            hass: Home Assistant instance.
            name: Name of the calendar.
            entity_id: Entity ID to assign.
            ical_events: Object managing les mises à jour du calendrier.
            filter_keyword: Mot clé pour filtrer l'événement (sur le sommaire).
        """
        self.entity_id = entity_id
        self._event = None
        self._name = name
        self._offset_reached = False
        self.ical_events = ical_events
        self._filter_keyword = filter_keyword.lower() if filter_keyword else ""
        # Vous pouvez ajouter d'autres attributs si besoin

    @property
    def extra_state_attributes(self):
        """Return the device state attributes."""
        return {"offset_reached": self._offset_reached}

    @property
    def event(self):
        """Return the next upcoming event."""
        return self._event

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    async def async_get_events(self, hass, start_date, end_date):
        """Get all events in a specific time frame."""
        _LOGGER.debug("Running ICalCalendarEventDevice async_get_events")
        return await self.ical_events.async_get_events(hass, start_date, end_date)

    async def async_update(self):
        """Update event data."""
        _LOGGER.debug("Running ICalCalendarEventDevice async_update for %s", self.name)
        await self.ical_events.update()

        event = copy.deepcopy(self.ical_events.event)
        if event is None:
            self._event = None
            return

        # Appliquer le filtrage par mot clé sur le sommaire de l'événement si un filtre est défini
        if self._filter_keyword:
            summary = event.get("summary", "").lower()
            if self._filter_keyword not in summary:
                _LOGGER.debug("L'événement '%s' ne contient pas le mot clé '%s' – il est ignoré", event.get("summary"), self._filter_keyword)
                self._event = None
                return

        (summary, offset) = extract_offset(event["summary"], OFFSET)
        event["summary"] = summary
        self._offset_reached = is_offset_reached(event["start"], offset)
        self._event = CalendarEvent(
            event["start"],
            event["end"],
            event["summary"],
            event.get("description", ""),
            event.get("location", "")
        )
