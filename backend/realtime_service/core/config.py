"""The settings only this service has. [F-2] #42

The ones every service shares (JWT, cookie name, CORS) are in
`shared.config.ServiceSettings`. [F-6] #76
"""

from functools import lru_cache

from pydantic import Field

from shared.config import ServiceSettings


class Settings(ServiceSettings):
    # Required, no default: pointed at the wrong Redis this service starts,
    # reports `ok` and relays nothing. D-046.
    # Form: redis://[:PASSWORD@]HOST:PORT/DB
    redis_url: str = Field(min_length=1)

    # Markets one socket may watch. Capped because each subscription costs a
    # fan-out iteration per event, and subscribing in a loop is the cheapest
    # denial of service against a server that holds connections open.
    max_subscriptions_per_connection: int = Field(default=50, gt=0)

    # Undelivered frames one connection may bank before it is closed with
    # 4409. A slow consumer is dropped, not buffered: ADR 0010.
    send_queue_size: int = Field(default=64, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
