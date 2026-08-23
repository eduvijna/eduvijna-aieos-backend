"""NATS JetStream publisher gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from nats.aio.client import Client as NATSClient
from nats.errors import Error as NatsError
from nats.js.errors import APIError

from aieos.platform.events.cloudevents import canonical_cloudevent_bytes
from aieos.platform.events.constants import (
    ERROR_NATS_PUBLISH_REJECTED,
    ERROR_NATS_STREAM_MISMATCH,
    ERROR_NATS_UNAVAILABLE,
)
from aieos.platform.events.models import OutboxMessage


@dataclass(frozen=True, slots=True)
class PublishAck:
    stream: str
    sequence: int


@dataclass(frozen=True, slots=True)
class PublishResult:
    published: bool
    ack: PublishAck | None = None
    error_code: str | None = None
    permanent: bool = False


class EventPublisher(Protocol):
    async def publish(self, message: OutboxMessage) -> PublishResult: ...


class NatsJetStreamEventPublisher:
    def __init__(
        self,
        client: NATSClient,
        *,
        expected_stream: str | None = None,
    ) -> None:
        self._client = client
        self._expected_stream = expected_stream

    async def publish(self, message: OutboxMessage) -> PublishResult:
        body = canonical_cloudevent_bytes(message.envelope)
        headers = {
            "Content-Type": "application/cloudevents+json",
            "Nats-Msg-Id": str(message.event_id),
        }
        try:
            js = self._client.jetstream()
            ack = await js.publish(
                message.event_type,
                body,
                headers=headers,
            )
            stream_name = str(ack.stream)
            sequence = int(ack.seq)
            if (
                self._expected_stream is not None
                and stream_name != self._expected_stream
            ):
                return PublishResult(
                    published=False,
                    ack=PublishAck(stream=stream_name, sequence=sequence),
                    error_code=ERROR_NATS_STREAM_MISMATCH,
                    permanent=True,
                )
            return PublishResult(
                published=True,
                ack=PublishAck(stream=stream_name, sequence=sequence),
            )
        except APIError:
            return PublishResult(
                published=False,
                error_code=ERROR_NATS_PUBLISH_REJECTED,
                permanent=True,
            )
        except (NatsError, OSError, TimeoutError):
            return PublishResult(
                published=False,
                error_code=ERROR_NATS_UNAVAILABLE,
                permanent=False,
            )
