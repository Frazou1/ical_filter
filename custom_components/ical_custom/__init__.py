"""The ical_custom integration."""

import asyncio
from datetime import datetime, timedelta
import logging
from urllib.parse import urlparse

from dateutil.rrule import rruleset, rrulestr
from dateutil.tz import gettz, tzutc
import icalendar
import voluptuous as vol

from homeassistant.components.calendar import CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import Throttle, dt as dt_util

from .const import CONF_DAYS, CONF_MAX_EVENTS, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)

# Liste des plateformes prises en charge par cette intégration.
PLATFORMS = ["sensor", "calendar"]

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=120)


def setup(hass: HomeAssistant, config):
    """Set up this integration with config flow."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up ical_custom from a config entry."""
    config = entry.data
    _LOGGER.debug("Running async_setup_entry for calendar %s", config.get(CONF_NAME))
    # Stocker l'objet d'API ou d'événements pour que les plateformes puissent y accéder
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    hass.data[DOMAIN][config.get(CONF_NAME)] = ICalEvents(hass=hass, config=config)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    config = entry.data
    _LOGGER.debug("Running async_unload_entry for calendar %s", config.get(CONF_NAME))
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if unload_ok:
        hass.data[DOMAIN].pop(config.get(CONF_NAME))

    return unload_ok


class ICalEvents:
    """Get a list of events."""

    def __init__(self, hass: HomeAssistant, config):
        """Set up a calendar object."""
        self.hass = hass
        self.name = config.get(CONF_NAME)
        self.url = config.get(CONF_URL)
        self.max_events = config.get(CONF_MAX_EVENTS)
        self.days = config.get(CONF_DAYS)
        self.verify_ssl = config.get(CONF_VERIFY_SSL)
        self.calendar = []
        self.event = None
        self.all_day = False

    async def async_get_events(self, hass: HomeAssistant, start_date, end_date):
        """Get list of upcoming events."""
        _LOGGER.debug("Running ICalEvents async_get_events")
        events = []
        if len(self.calendar) > 0:
            for event in self.calendar:
                _LOGGER.debug(
                    "Checking if event %s with start %s and end %s is within %s and %s",
                    event["summary"],
                    event["start"],
                    event["end"],
                    start_date,
                    end_date,
                )
                if event["start"] < end_date and event["end"] > start_date:
                    events.append(
                        CalendarEvent(
                            event["start"],
                            event["end"],
                            event["summary"],
                            event["description"],
                            event["location"],
                        )
                    )
        return events

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def update(self):
        """Update list of upcoming events."""
        _LOGGER.debug("Running ICalEvents update for calendar %s", self.name)
        parts = urlparse(self.url)
        if parts.scheme == "file":
            with open(parts.path) as f:
                text = f.read()
        else:
            if parts.scheme == "webcal":
                # Remplacer webcal par https
                self.url = parts.geturl().replace("webcal", "https", 1)
            session = async_get_clientsession(self.hass, verify_ssl=self.verify_ssl)
            async with session.get(self.url) as response:
                text = await response.text()
        if text is not None:
            # Supprimer les NULL-bytes qui pourraient casser le parsing
            event_list = icalendar.Calendar.from_ical(text.replace("\x00", ""))
            start_of_events = dt_util.start_of_local_day()
            end_of_events = dt_util.start_of_local_day() + timedelta(days=self.days)
            self.calendar = await self._ical_parser(event_list, start_of_events, end_of_events)

        if len(self.calendar) > 0:
            found_next_event = False
            for event in self.calendar:
                if event["end"] > dt_util.now() and not found_next_event:
                    _LOGGER.debug(
                        "Event %s is the first event with end in the future: %s",
                        event["summary"],
                        event["end"],
                    )
                    self.event = event
                    found_next_event = True

    async def _ical_parser(self, calendar, from_date, to_date):
        """Return a sorted list of events from an icalendar object."""
        events = []
        for event in calendar.walk("VEVENT"):
            if "RRULE" in event:
                rrule = event["RRULE"]
                start_rules = rruleset()
                end_rules = rruleset()
                if "UNTIL" in rrule:
                    try:
                        if rrule["UNTIL"][0] < from_date - timedelta(days=30):
                            continue
                    except Exception:
                        pass
                    _LOGGER.debug("UNTIL in rrule: %s", str(rrule["UNTIL"]))
                    until = await self._ical_date_fixer(rrule["UNTIL"], "UTC")
                    rrule["UNTIL"] = [until]
                else:
                    _LOGGER.debug("No UNTIL in rrule")
                _LOGGER.debug("DTSTART in rrule: %s", str(event["DTSTART"].dt))
                dtstart = await self._ical_date_fixer(event["DTSTART"].dt, dt_util.DEFAULT_TIME_ZONE)
                if "DTEND" not in event:
                    _LOGGER.debug("Event found without end datetime")
                    if self.all_day:
                        dtend = dtstart + timedelta(days=1, seconds=-1)
                    else:
                        dtend = dtstart
                else:
                    _LOGGER.debug("DTEND in event")
                    dtend = await self._ical_date_fixer(event["DTEND"].dt, dt_util.DEFAULT_TIME_ZONE)
                try:
                    start_rules.rrule(rrulestr(rrule.to_ical().decode("utf-8"), dtstart=dtstart))
                except Exception as e:
                    _LOGGER.error(
                        "Exception %s in start_rules.rrule: %s - Start: %s - RRule: %s",
                        str(e),
                        str(event["SUMMARY"]),
                        str(dtstart),
                        str(event["RRULE"]),
                    )
                    continue
                try:
                    end_rules.rrule(rrulestr(rrule.to_ical().decode("utf-8"), dtstart=dtend))
                except Exception as e:
                    _LOGGER.error(
                        "Exception %s in end_rules.rrule: %s - End: %s - RRule: %s",
                        str(e),
                        str(event["SUMMARY"]),
                        str(dtend),
                        str(event["RRULE"]),
                    )
                    end_rules = start_rules
                try:
                    if "EXDATE" in event:
                        if isinstance(event["EXDATE"], list):
                            for exdate in event["EXDATE"]:
                                for edate in exdate.dts:
                                    start_rules.exdate(edate.dt)
                                    end_rules.exdate(edate.dt)
                        else:
                            for edate in event["EXDATE"].dts:
                                start_rules.exdate(edate.dt)
                                end_rules.exdate(edate.dt)
                except Exception as e:
                    _LOGGER.error(
                        "Exception %s in EXDATE: %s - Start: %s - RRule: %s - EXDate: %s",
                        str(e),
                        str(event["SUMMARY"]),
                        str(dtstart),
                        str(event["RRULE"]),
                        str(event["EXDATE"]),
                    )
                    continue
                try:
                    starts = start_rules.between(after=(from_date - timedelta(days=7)), before=to_date)
                    ends = end_rules.between(after=(from_date - timedelta(days=7)), before=to_date)
                except Exception as e:
                    _LOGGER.error(
                        "Exception %s in starts/ends: %s - Start: %s - End: %s, RRule: %s",
                        str(e),
                        str(event["SUMMARY"]),
                        str(dtstart),
                        str(dtend),
                        str(event["RRULE"]),
                    )
                    continue
                if len(starts) < 1:
                    _LOGGER.debug("Event does not happen within our limits")
                    continue
                ends.reverse()
                for start in starts:
                    if len(ends) == 0:
                        continue
                    end = ends.pop()
                    event_dict = self._ical_event_dict(start, end, from_date, event)
                    if event_dict:
                        events.append(event_dict)
                _LOGGER.debug("Done parsing RRULE")
            else:
                try:
                    if "DTEND" in event and event["DTEND"].dt.date() < from_date.date() - timedelta(days=30):
                        continue
                except Exception:
                    pass
                try:
                    if "DTEND" in event and event["DTEND"].dt < from_date.date() - timedelta(days=30):
                        continue
                except Exception:
                    pass
                _LOGGER.debug("DTSTART in event: {}".format(event["DTSTART"].dt))
                dtstart = await self._ical_date_fixer(event["DTSTART"].dt, dt_util.DEFAULT_TIME_ZONE)
                start = dtstart
                if "DTEND" not in event:
                    _LOGGER.debug("Event found without end datetime")
                    if self.all_day:
                        dtend = dtstart + timedelta(days=1, seconds=-1)
                    else:
                        dtend = dtstart
                else:
                    _LOGGER.debug("DTEND in event")
                    dtend = await self._ical_date_fixer(event["DTEND"].dt, dt_util.DEFAULT_TIME_ZONE)
                end = dtend
                event_dict = self._ical_event_dict(start, end, from_date, event)
                if event_dict:
                    events.append(event_dict)
        return sorted(events, key=lambda k: k["start"])

    def _ical_event_dict(self, start, end, from_date, event):
        """Ensure that events are within the start and end."""
        if end.date() < from_date.date():
            _LOGGER.debug("This event has already ended")
            return None
        if (
            end.date() == from_date.date()
            and end.hour == 0
            and end.minute == 0
            and end.second == 0
        ):
            _LOGGER.debug("This event has already ended")
            return None
        _LOGGER.debug(
            "Start: %s Tzinfo: %s Default: %s StartAs %s",
            str(start),
            str(start.tzinfo),
            dt_util.DEFAULT_TIME_ZONE,
            start.astimezone(dt_util.DEFAULT_TIME_ZONE),
        )
        event_dict = {
            "summary": event.get("SUMMARY", "Unknown"),
            "start": start.astimezone(dt_util.DEFAULT_TIME_ZONE),
            "end": end.astimezone(dt_util.DEFAULT_TIME_ZONE),
            "location": event.get("LOCATION"),
            "description": event.get("DESCRIPTION"),
            "all_day": self.all_day,
        }
        _LOGGER.debug("Event to add: %s", str(event_dict))
        return event_dict

    async def _ical_date_fixer(self, indate, timezone="UTC"):
        """Convert a date/datetime to a timezone-aware datetime-object."""
        self.all_day = False
        _LOGGER.debug("Fixing date: %s in TZ %s", str(indate), str(timezone))
        if isinstance(indate, list):
            indate = indate[0]
        if not isinstance(indate, datetime):
            try:
                self.all_day = True
                indate = await self.hass.async_add_executor_job(
                    datetime, indate.year, indate.month, indate.day, 0, 0, 0
                )
            except Exception as e:
                _LOGGER.error("Unable to parse indate: %s", str(e))
        indate_replaced = await self.hass.async_add_executor_job(
            self._date_replace, indate, timezone
        )
        _LOGGER.debug("Out date: %s", str(indate_replaced))
        return indate_replaced

    def _date_replace(self, indate: datetime, timezone):
        """Replace tzinfo in a datetime object."""
        if indate.tzinfo is None or indate.tzinfo.utcoffset(indate) is None:
            return indate.replace(tzinfo=gettz(str(timezone)))
        if not str(indate.tzinfo).startswith("tzfile"):
            return indate.replace(tzinfo=gettz(str(indate.tzinfo)))
        if str(indate.tzinfo).endswith("/UTC"):
            return indate.replace(tzinfo=tzutc)
        return None
