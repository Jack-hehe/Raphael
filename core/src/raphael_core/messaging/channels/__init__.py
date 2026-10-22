"""The messaging apps Raphael can be reached through."""

from .base import ChannelType
from .telegram import TELEGRAM

CHANNEL_TYPES: dict[str, ChannelType] = {TELEGRAM.kind: TELEGRAM}
