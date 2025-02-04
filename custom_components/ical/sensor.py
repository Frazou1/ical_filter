"""Création de capteurs pour les événements futurs filtrés."""

from datetime import datetime, timedelta
import logging

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity, generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MAX_EVENTS, DOMAIN, ICON

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant, config, async_add_entities, discovery_info=None
):
    # Méthode héritée mais non utilisée si l'intégration est configurée par config flow
    return True


async def async_setup_entry(
    hass: HomeAssistant, config_entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Configure l'intégration iCal et crée les capteurs dynamiquement."""
    config = config_entry.data
    name = config.get(CONF_NAME)
    max_events = config.get(CONF_MAX_EVENTS)

    # Récupération de l'objet ical_events (qui contient la liste des événements)
    ical_events = hass.data[DOMAIN][name]
    await ical_events.update()
    if ical_events.calendar is None:
        _LOGGER.error("Impossible de récupérer l'iCal")
        return False

    sensors = []
    # Création d'un nombre fixe de capteurs, de 0 à max_events-1.
    for eventnumber in range(max_events):
        sensors.append(ICalSensor(hass, ical_events, f"{DOMAIN} {name}", eventnumber))
    async_add_entities(sensors)


class ICalSensor(Entity):
    """Capteur iCal pour un événement filtré dont le summary contient 'Rosalie Fraser'.

    Chaque instance représente l'événement à l'index donné dans la liste filtrée.
    """

    def __init__(self, hass: HomeAssistant, ical_events, sensor_name, event_number) -> None:
        """Initialise le capteur.
        
        - **ical_events** : l'objet contenant les événements du calendrier.
        - **event_number** : l'indice de l'événement dans la liste filtrée.
        """
        super().__init__()
        self._ical_events = ical_events
        self._event_number = event_number
        self._hass = hass
        self._entity_id = generate_entity_id(
            "sensor.{}", f"{sensor_name} event {self._event_number}", hass=self._hass
        )
        self._event_attributes = {
            "summary": None,
            "description": None,
            "location": None,
            "start": None,
            "end": None,
            "eta": None,
        }
        self._state = None

    @property
    def unique_id(self) -> str:
        """Retourne l'ID unique du capteur."""
        return f"{self._ical_events.name.lower()}_event_{self._event_number}"

    @property
    def name(self):
        """Retourne le nom du capteur (basé sur le summary de l'événement s'il existe)."""
        return self._event_attributes["summary"] or f"{self._ical_events.name} event {self._event_number}"

    @property
    def icon(self):
        """Retourne l'icône pour l'interface."""
        return ICON

    @property
    def state(self):
        """Retourne l'état du capteur."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Retourne les attributs détaillés de l'événement."""
        return self._event_attributes

    @property
    def available(self):
        """Le capteur est disponible si un événement est assigné."""
        return self._event_attributes["start"] is not None

    async def async_update(self):
        """Met à jour le capteur en recalculant le filtrage sur la liste d'événements."""
        _LOGGER.debug("Mise à jour du capteur iCal pour l'indice %s", self._event_number)

        # Mise à jour de l'objet ical_events
        await self._ical_events.update()

        # Filtrer les événements dont le champ 'summary' contient "Rosalie Fraser"
        filtered_events = [
            event for event in self._ical_events.calendar
            if "Rosalie Fraser" in event.get("summary", "")
        ]

        if self._event_number < len(filtered_events):
            event = filtered_events[self._event_number]
            name = event.get("summary", "Inconnu")
            start = event.get("start")

            _LOGGER.debug(
                "Mise à jour de l'événement '%s' (index %s) : Début %s, Fin %s",
                name, self._event_number, event.get("start"), event.get("end")
            )

            self._event_attributes["summary"] = name
            self._event_attributes["start"] = start
            self._event_attributes["end"] = event.get("end")
            self._event_attributes["location"] = event.get("location", "")
            self._event_attributes["description"] = event.get("description", "")
            # Calcul de l'ETA (en jours)
            self._event_attributes["eta"] = (
                start - datetime.now(start.tzinfo) + timedelta(days=1)
            ).days
            self._event_attributes["all_day"] = event.get("all_day")

            # Constitution de l'état (affichage de la date et de l'heure si ce n'est pas un événement sur toute la journée)
            self._state = f"{name} - {start.strftime('%-d %B %Y')}"
            if not event.get("all_day"):
                self._state += f" {start.strftime('%H:%M')}"
        else:
            # Aucun événement correspondant pour cet indice
            self._event_attributes = {
                "summary": None,
                "description": None,
                "location": None,
                "start": None,
                "end": None,
                "eta": None,
            }
            self._state = None
