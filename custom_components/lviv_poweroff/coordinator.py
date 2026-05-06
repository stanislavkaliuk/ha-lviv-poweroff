"""Provides the LvivPowerOffCoordinator class for polling power off periods."""

from datetime import datetime, timedelta
import logging

from homeassistant.components.calendar import CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, POWEROFF_GROUP_CONF, UPDATE_INTERVAL, PowerOffGroup, STATE_ON, STATE_OFF
from .loe_scrapper import LoeScrapper
from .entities import PowerOffPeriod

LOGGER = logging.getLogger(__name__)

TIMEFRAME_TO_CHECK = timedelta(hours=24)


class LvivPowerOffCoordinator(DataUpdateCoordinator):
    """Coordinates the polling of power off periods."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.hass = hass
        self.config_entry = config_entry
        self.group: PowerOffGroup = config_entry.data[POWEROFF_GROUP_CONF]
        self.api = LoeScrapper(self.group, dt_util.get_default_time_zone())
        self.periods: list[PowerOffPeriod] = []
        self._last_valid_periods: list[PowerOffPeriod] = []
        self.last_success_update: datetime | None = None

    async def _async_update_data(self) -> dict:
        """Fetch power off periods from scrapper."""
        try:
            await self._fetch_periods()
            self.last_success_update = dt_util.now()
            LOGGER.debug("PowerOff data updated at %s", self.last_success_update)
            return {}
        except Exception as err:
            LOGGER.exception("Cannot obtain power offs periods for group %s", self.group)
            msg = f"Power offs not polled: {err}"
            raise UpdateFailed(msg) from err

    async def _fetch_periods(self) -> None:
        LOGGER.debug("Fetching power off periods for group %s", self.group)
        periods = await self.api.get_power_off_periods()

        if not periods:
            LOGGER.debug("Empty periods received, keeping last valid periods")
            if self._last_valid_periods:
                self.periods = self._last_valid_periods
            return

        self.periods = periods
        self._last_valid_periods = periods

    def _get_next_power_change_dt(self, on: bool) -> datetime | None:
        """Get the next power on/off."""
        now = dt_util.now()
        events = self.get_events_between(
            now,
            now + TIMEFRAME_TO_CHECK,
        )
        if on:
            dts = sorted(event.end for event in events)
        else:
            dts = sorted(event.start for event in events)
        LOGGER.debug("Next dts: %s", dts)
        for dt in dts:
            if dt > now:
                return dt  # type: ignore
        return None

    @property
    def next_poweroff(self) -> datetime | None:
        """Get the next poweroff time."""
        dt = self._get_next_power_change_dt(on=False)
        LOGGER.debug("Next poweroff: %s", dt)
        return dt

    @property
    def next_poweron(self) -> datetime | None:
        """Get next connectivity time."""
        dt = self._get_next_power_change_dt(on=True)
        LOGGER.debug("Next powerof: %s", dt)
        return dt

    @property
    def current_state(self) -> str:
        """Get the current state."""
        now = dt_util.now()
        event = self.get_event_at(now)
        return STATE_OFF if event else STATE_ON

    def get_event_at(self, at: datetime) -> CalendarEvent | None:
        """Get the current event."""
        for period in self.periods:
            if period.start_datetime <= at <= period.end_datetime:
                return self._get_calendar_event(period.start_datetime, period.end_datetime)
        return None

    def get_events_between(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Get all events."""
        events = []
        for period in self.periods:
            if start_date <= period.start_datetime <= end_date or start_date <= period.end_datetime <= end_date:
                events.append(self._get_calendar_event(period.start_datetime, period.end_datetime))
        return events

    def _get_calendar_event(self, start: datetime, end: datetime) -> CalendarEvent:
        return CalendarEvent(
            start=start,
            end=end,
            summary=STATE_OFF,
        )
