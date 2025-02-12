"""L'intégration ical."""
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

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA
)

# TODO Répertoriez les plateformes que vous souhaitez prendre en charge.
# Pour votre PR initial, limitez-le à 1 plateforme.
PLATFORMS = ["sensor"]
# PLATFORMS = ["sensor"]

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=120)


def setup(hass: HomeAssistant, config):
    """Configurer cette intégration avec le flux de configuration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Configurer ical à partir d'une entrée de configuration."""
    config = entry.data
    _LOGGER.debug(
        "Exécution de l'initialisation async_setup_entry pour le calendrier %s",
        config.get(CONF_NAME)
    )
    # TODO Stockez un objet API auquel vos plateformes pourront accéder
    # hass.data[DOMAIN][entry.entry_id] = MyApi(...)
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    hass.data[DOMAIN][config.get(CONF_NAME)] = ICalEvents(hass=hass, config=config)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Décharger une entrée de configuration."""
    config = entry.data
    _LOGGER.debug("Exécution de async_unload_entry pour le calendrier %s", config.get(CONF_NAME))
    unload_ok = all(
        await asyncio.gather(
            *[hass.config_entries.async_forward_entry_unload(entry, component) for component in PLATFORMS]
        )
    )
    if unload_ok:
        hass.data[DOMAIN].pop(config.get(CONF_NAME))
    return unload_ok


class ICalEvents:
    """Obtenir une liste d'événements."""

    def __init__(self, hass: HomeAssistant, config):
        """Configurer un objet de calendrier."""
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
        """Obtenir la liste des événements à venir."""
        _LOGGER.debug("Exécution d'ICalEvents async_get_events")
        events = []
        if len(self.calendar) > 0:
            for event in self.calendar:
                _LOGGER.debug(
                    "Vérification si l'événement %s a un début %s et une fin %s dans la limite : %s et %s",
                    event["summary"], event["start"], event["end"], start_date, end_date,
                )
                if event["start"] < end_date and event["end"] > start_date:
                    _LOGGER.debug("... et c'est le cas")
                    # classe de type fortement fix
                    events.append(
                        CalendarEvent(
                            event["start"],
                            event["end"],
                            event["summary"],
                            event["description"],
                            event["location"],
                        )
                    )
                    # events.append(event)
        return events

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def update(self):
        """Mettre à jour la liste des événements à venir."""
        _LOGGER.debug("Exécution de la mise à jour d'ICalEvents pour le calendrier %s", self.name)
        parts = urlparse(self.url)
        if parts.scheme == "file":
            with open(parts.path) as f:
                text = f.read()
        else:
            if parts.scheme == "webcal":
                # Il y a un problème potentiel ici si l'URL réelle est http, pas https
                self.url = parts.geturl().replace("webcal", "https", 1)
            session = async_get_clientsession(self.hass, verify_ssl=self.verify_ssl)
            async with session.get(self.url) as response:
                text = await response.text()
        if text is not None:
            # Certains calendriers sont pour une raison quelconque remplis d'octets NULL.
            # Ils interrompent l'analyse, nous nous en débarrassons donc
            event_list = icalendar.Calendar.from_ical(text.replace("\x00", ""))
            start_of_events = dt_util.start_of_local_day()
            end_of_events = dt_util.start_of_local_day() + timedelta(days=self.days)
            self.calendar = await self._ical_parser(event_list, start_of_events, end_of_events)
            if len(self.calendar) > 0:
                found_next_event = False
                for event in self.calendar:
                    if event["end"] > dt_util.now() and not found_next_event:
                        _LOGGER.debug(
                            "L'événement %s est le premier événement avec une fin dans le futur : %s",
                            event["summary"], event["end"],
                        )
                        self.event = event
                        found_next_event = True

    async def _ical_parser(self, calendar, from_date, to_date):
        """Renvoie une liste triée d'événements à partir d'un objet icalendar."""
        events = []
        for event in calendar.walk("VEVENT"):
            # Les RRULE s'avèrent plus difficiles qu'on ne le pensait initialement.
            # Cela est principalement dû à la gestion par Python des horodatages TZ-naifs et TZ-aware, et aux incohérences
            # dans la façon dont les RRULE sont implémentés dans la bibliothèque icalendar.
            if "RRULE" in event:
                # _LOGGER.debug("RRULE in event: %s", str(event["SUMMARY"]))
                rrule = event["RRULE"]
                # Puisque nous n'obtenons pas à la fois le début et la fin dans un seul objet, nous devons générer deux listes,
                # Une de tous les DTSTART et une autre liste de tous les DTEND
                start_rules = rruleset()
                end_rules = rruleset()
                if "UNTIL" in rrule:
                    try:
                        # Ignorez simplement les événements qui se sont terminés il y a longtemps
                        if rrule["UNTIL"][0] < from_date - timedelta(days=30):
                            # _LOGGER.debug("Ancien événement 1 %s - terminé %s", event["SUMMARY"], str(rrule["UNTIL"][0]))
                            continue
                    except Exception:
                        pass
                    _LOGGER.debug("UNTIL in rrule: %s", str(rrule["UNTIL"]))
                    # Assurez-vous que UNTIL est compatible tz et en UTC
                    # (Tous les icalendar ne l'implémentent pas correctement)
                    until = await self._ical_date_fixer(rrule["UNTIL"], "UTC")
                    rrule["UNTIL"] = [until]
                else:
                    _LOGGER.debug("Pas de UNTIL dans rrule")
                _LOGGER.debug("DTSTART dans rrule: %s", str(event["DTSTART"].dt))
                dtstart = await self._ical_date_fixer(
                    event["DTSTART"].dt, dt_util.DEFAULT_TIME_ZONE
                )
                if "DTEND" not in event:
                    _LOGGER.debug("Event found without end datetime")
                    if self.all_day:
                        # S'il s'agit d'un événement d'une journée entière sans heure de fin indiquée, nous supposerons qu'il se termine à 23:59:59
                        _LOGGER.debug(
                            f"L'événement {event['SUMMARY']} est marqué comme étant toute la journée, avec une heure de début de {dtstart}."
                        )
                        dtend = dtstart + timedelta(days=1, seconds=-1)
                        _LOGGER.debug(f"Définition de l'heure de fin à {dtend}")
                    else:
                        _LOGGER.debug(
                            f"L'événement {event['SUMMARY']} n'a pas de fin mais n'est pas marqué comme étant toute la journée."
                        )
                        dtend = dtstart
                else:
                    _LOGGER.debug("DTEND dans l'événement")
                    dtend = await self._ical_date_fixer(
                        event["DTEND"].dt, dt_util.DEFAULT_TIME_ZONE
                    )
                # Nous espérons donc avoir maintenant un dtstart approprié que nous pouvons utiliser pour créer les heures de début en fonction de la règle
                # _LOGGER.debug("RRulestr %s", rrule.to_ical().decode("utf-8"))
                try:
                    start_rules.rrule(
                        rrulestr(rrule.to_ical().decode("utf-8"), dtstart=dtstart)
                    )
                except Exception as e:
                    # Si cela échoue, passez à l'événement suivant
                    _LOGGER.error(
                        "Exception %s in start_rules.rrule: %s - Start: %s - RRule: %s",
                        str(e), str(event["SUMMARY"]), str(dtstart), str(event["RRULE"]),
                    )
                    continue
                # _LOGGER.debug("Start rules %s", str(list(start_rules)))
                # ... Et la même chose pour end_rules
                try:
                    end_rules.rrule(
                        rrulestr(rrule.to_ical().decode("utf-8"), dtstart=dtend)
                    )
                except Exception as e:
                    # Si cela échoue, utilisez simplement les règles de démarrage
                    _LOGGER.error(
                        "Exception %s in end_rules.rrule: %s - Fin: %s - RRule: %s",
                        str(e), str(event["SUMMARY"]), str(dtend), str(event["RRULE"]),
                    )
                    end_rules = start_rules
                # Les EXDATE sont difficiles à analyser. Il peut s'agir d'une liste ou d'un simple objet.
                # Ils peuvent contenir des données TZ, ou non...
                # Nous faisons simplement de notre mieux et interceptons l'exception lorsqu'elle échoue et passons à l'événement suivant.
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
                        str(e), str(event["SUMMARY"]), str(dtstart), str(event["RRULE"]), str(event["EXDATE"]),
                    )
                    continue
                # Obtenons tous les événements générés par RRULE qui commenceront 7 jours avant aujourd'hui et se termineront avant to_date
                # pour nous assurer que nous capturons (la plupart) des événements récurrents qui pourraient déjà avoir commencé.
                try:
                    starts = start_rules.between(
                        after=(from_date - timedelta(days=7)), before=to_date
                    )
                    ends = end_rules.between(
                        after=(from_date - timedelta(days=7)), before=to_date
                    )
                except Exception as e:
                    _LOGGER.error(
                        "Exception %s in starts/ends: %s - Start: %s - End: %s, RRule: %s",
                        str(e), str(event["SUMMARY"]), str(dtstart), str(dtend), str(event["RRULE"]),
                    )
                    continue
                # _LOGGER.debug("Starts: %s", str(starts))
                # Nous pourrions obtenir des RRULE qui ne se situent pas dans les limites ci-dessus, ignorons-les simplement
                if len(starts) < 1:
                    _LOGGER.debug("Event does not happen within our boundaries")
                    continue
                # Il doit y avoir une meilleure façon de faire ça... Mais au moins, cela semble fonctionner pour l'instant.
                ends.reverse()
                for start in starts:
                    # Parfois, nous n'obtenons pas le même nombre de débuts et de fins...
                    if len(ends) == 0:
                        continue
                    end = ends.pop()
                    event_dict = self._ical_event_dict(start, end, from_date, event)
                    if event_dict:
                        events.append(event_dict)
                _LOGGER.debug("Done parsing RRULE")
            else:
                # Utilisons la même magie que pour les règles de référence pour obtenir cela (aussi) correct que possible
                try:
                    # Ignorer simplement les événements qui se sont terminés il y a longtemps
                    if "DTEND" in event and event["DTEND"].dt.date() < from_date.date() - timedelta(days=30):
                        # _LOGGER.debug("Ancien événement 1 %s - terminé %s", event["SUMMARY"], str(event["DTEND"].dt))
                        continue
                except Exception:
                    pass
                try:
                    if "DTEND" in event and event["DTEND"].dt < from_date.date() - timedelta(days=30):
                        # _LOGGER.debug("Ancien événement 2 %s - terminé %s", event["SUMMARY"], str(event["DTEND"].dt))
                        continue
                except Exception:
                    pass
                _LOGGER.debug("DTSTART in event: %s", event["DTSTART"].dt)
                dtstart = await self._ical_date_fixer(
                    event["DTSTART"].dt, dt_util.DEFAULT_TIME_ZONE
                )
                start = dtstart
                if "DTEND" not in event:
                    _LOGGER.debug("Evénement trouvé sans date et heure de fin")
                    if self.all_day:
                        # S'il s'agit d'un événement d'une journée entière sans heure de fin indiquée, nous supposerons qu'il se termine à 23:59:59
                        _LOGGER.debug(
                            f"L'événement {event['SUMMARY']} est marqué comme étant de la journée entière, avec une heure de début de {start}."
                        )
                        dtend = dtstart + timedelta(days=1, seconds=-1)
                        _LOGGER.debug(f"Définition de l'heure de fin sur {dtend}")
                    else:
                        _LOGGER.debug(
                            f"L'événement {event['SUMMARY']} n'a pas de fin mais n'est pas marqué comme étant de la journée entière."
                        )
                        dtend = dtstart
                else:
                    _LOGGER.debug("DTEND dans l'événement")
                    dtend = await self._ical_date_fixer(
                        event["DTEND"].dt, dt_util.DEFAULT_TIME_ZONE
                    )
                end = dtend
                event_dict = self._ical_event_dict(start, end, from_date, event)
                if event_dict:
                    events.append(event_dict)
        return sorted(events, key=lambda k: k["start"])

    def _ical_event_dict(self, start, end, from_date, event):
        """Assurez-vous que les événements sont compris entre le début et la fin."""
        # Ignorer cet événement s'il est dans le passé
        if end.date() < from_date.date():
            _LOGGER.debug("Cet événement est déjà terminé")
            return None
        # Ignorer les événements qui se sont terminés exactement à minuit
        if (end.date() == from_date.date() and end.hour == 0 and end.minute == 0 and end.second == 0):
            _LOGGER.debug("Cet événement est déjà terminé")
            return None
        _LOGGER.debug(
            "Début : %s Tzinfo : %s Par défaut : %s StartAs %s",
            str(start),
            str(start.tzinfo),
            dt_util.DEFAULT_TIME_ZONE,
            start.astimezone(dt_util.DEFAULT_TIME_ZONE),
        )
        event_dict = {
            "summary": event.get("SUMMARY", "Inconnu"),
            "start": start.astimezone(dt_util.DEFAULT_TIME_ZONE),
            "end": end.astimezone(dt_util.DEFAULT_TIME_ZONE),
            "location": event.get("LOCATION"),
            "description": event.get("DESCRIPTION"),
            "all_day": self.all_day,
        }
        _LOGGER.debug("Evénement à ajouter : %s", str(event_dict))
        return event_dict

    async def _ical_date_fixer(self, indate, timezone="UTC"):
        """Convertir quelque chose qui ressemble à une date ou une datetime en un objet datetime prenant en compte le fuseau horaire."""
        self.all_day = False
        _LOGGER.debug("Fixation de la date : %s dans TZ %s", str(indate), str(timezone))
        # indate peut être une entrée unique ou une liste avec un élément...
        if isinstance(indate, list):
            indate = indate[0]
        # indate peut être une date sans heure...
        if not isinstance(indate, datetime):
            try:
                self.all_day = True
                indate = await self.hass.async_add_executor_job(
                    datetime, indate.year, indate.month, indate.day, 0, 0, 0
                )
            except Exception as e:
                _LOGGER.error("Impossible d'analyser indate : %s", str(e))
        indate_replaced = await self.hass.async_add_executor_job(self._date_replace, indate, timezone)
        _LOGGER.debug("Date d'expiration : %s", str(indate_replaced))
        return indate_replaced

    def _date_replace(self, indate: datetime, timezone):
        """Remplacer tzinfo dans un objet datetime."""
        # indate peut être TZ-naïf
        if indate.tzinfo is None or indate.tzinfo.utcoffset(indate) is None:
            # _LOGGER.debug("TZ-Naïf indate : %s Ajout de TZ %s", str(indate), str(gettz(str(timezone))))
            return indate.replace(tzinfo=gettz(str(timezone)))
        else:
            if not str(indate.tzinfo).startswith("tzfile"):
                # _LOGGER.debug("Pytz indate: %s. Remplacement par tz %s", str(indate), str(gettz(str(indate.tzinfo))))
                return indate.replace(tzinfo=gettz(str(indate.tzinfo)))
            if str(indate.tzinfo).endswith("/UTC"):
                return indate.replace(tzinfo=tzutc)
            # _LOGGER.debug("Tzinfo 2: %s", str(indate.tzinfo))
            return None
