# iCal Sensor filter Support for Home Assistant custom

This integration will create sensors for the next few future calendar events, called:

* sensor.ical_custom_my_calendar_event_0
* sensor.ical_custom_my_calendar_event_1
* sensor.ical_custom_my_calendar_event_2
(...)



## Installation

Install with HACS


### Setup

The integration is set up using the GUI.

* Go to Configuration -> Integrations and click on the "+"-button.
* Search for "ical_custom"
* Enter a name for the calendar, and the URL
* By default it will set up 5 sensors for the 5 nex upcoming events (sensor.ical_custom<calendar_name>_event_1 ~ 5).  You can adjust this to add more or fewer sensors
* Enter a Filter_keyword to search in the sumary of the event
* The integration will only consider events with a start time 365 days into the future by default. This can also be adjusted when adding a new calendar

* ![image](https://github.com/user-attachments/assets/40ffae05-7654-4181-bec6-e9e82dfe21f0)


