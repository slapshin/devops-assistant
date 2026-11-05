"""Channel drivers by kind (T026).

Adding a messenger: a ``ChannelKind`` value and destination models in
``app/domain/notifications.py``, a package here implementing ``ChannelDriver``, and an entry
in ``build_drivers``. A driver the server cannot deliver with is still registered and says why
(``unavailable()``), so the UI can explain it.
"""

from app.domain.notifications import ChannelKind
from app.notifications.drivers.base import ChannelDriver
from app.notifications.drivers.email.driver import EmailDriver
from app.notifications.drivers.email.transport import sender_address, transport_from_settings
from app.notifications.drivers.telegram.client import BotApiClient
from app.notifications.drivers.telegram.driver import ApiFactory, TelegramDriver
from app.notifications.drivers.telegram.fake import FakeTelegramApi
from app.settings import Settings, TelegramProvider

Drivers = dict[ChannelKind, ChannelDriver]


def telegram_api(settings: Settings) -> ApiFactory | None:
    match settings.telegram_provider:
        case TelegramProvider.BOT_API:
            return lambda token: BotApiClient(settings.telegram_api_url, token)
        case TelegramProvider.FAKE:
            return lambda _token: FakeTelegramApi(settings.telegram_outbox_dir)
        case TelegramProvider.NONE:
            return None


def build_drivers(settings: Settings) -> Drivers:
    return {
        ChannelKind.EMAIL: EmailDriver(transport_from_settings(settings), sender_address(settings)),
        ChannelKind.TELEGRAM: TelegramDriver(telegram_api(settings)),
    }
