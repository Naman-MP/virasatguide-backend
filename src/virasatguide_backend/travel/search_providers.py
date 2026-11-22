"""Provider contracts. No inventory provider is registered without a tested account."""

from typing import Protocol

from .search_models import HotelOption, HotelSearchInput, Mode, TransportOption, TransportSearchInput


class TransportProvider(Protocol):
    """Contract for a licensed adapter that supplies complete priced journeys."""
    name: str
    modes: set[Mode]
    max_stops: int
    booking_hosts: set[str]

    async def is_ready(self) -> bool:
        """Authenticate with the real provider; key presence alone is insufficient."""
        ...

    def supports(self, request: TransportSearchInput) -> bool:
        """Check countries, dates, stops and party limits before querying inventory."""
        ...

    async def search(self, request: TransportSearchInput) -> list[TransportOption]:
        """Return complete requested journeys, with total prices for all travelers/legs."""
        ...


class HotelProvider(Protocol):
    """Contract for a licensed adapter that supplies stay inventory."""
    name: str
    booking_hosts: set[str]

    async def is_ready(self) -> bool:
        """Check authenticated provider readiness with its configured upstream service."""
        ...
    def supports(self, request: HotelSearchInput) -> bool:
        """Report whether the adapter can handle the requested stay."""
        ...

    async def search(self, request: HotelSearchInput) -> list[HotelOption]:
        """Prices cover all requested rooms; total_price covers the entire stay."""
        ...


# Register only licensed adapters whose authenticated calls have been verified.
transport_providers: list[TransportProvider] = []
hotel_providers: list[HotelProvider] = []
