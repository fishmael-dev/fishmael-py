import collections.abc
import dataclasses
import enum
import typing

import disagain

from fishmael import models

__all__: collections.abc.Sequence[str] = ("ShardStreamReader",)

StreamToLastSeenMap: typing.TypeAlias = dict[bytes | str, bytes]
StreamToClassMap: typing.TypeAlias = dict[bytes | str, type[models.protocol.Streamable]]


class ShardStreamReaderState(enum.Enum):
    DISCONNECTED = enum.auto()
    STREAMING = enum.auto()


@dataclasses.dataclass
class ShardStreamReader:
    connection: disagain.connection.ActionableConnection
    shard: models.ShardId

    _state: ShardStreamReaderState = dataclasses.field(
        default=ShardStreamReaderState.DISCONNECTED,
        init=False,
    )
    # Mapping of stream key to last-seen entry id in that stream
    _streams_to_last_seen: StreamToLastSeenMap = dataclasses.field(default_factory=dict, init=False)
    # Mapping of stream key to event class.
    _streams_to_class: StreamToClassMap = dataclasses.field(default_factory=dict, init=False)

    @classmethod
    def for_streams(
        cls,
        *desired_streams: type[models.protocol.Streamable],
        connection: disagain.connection.ActionableConnection,
        shard: models.ShardId,
    ) -> "ShardStreamReader":
        self = cls(connection, shard)
        self.add_streams(*desired_streams)
        return self

    def add_streams(self, *desired_streams: type[models.protocol.Streamable]) -> None:
        if self._state is ShardStreamReaderState.STREAMING:
            msg = "ShardStreamReader streams cannot be modified while streaming."
            raise RuntimeError(msg)

        # TODO: Get and store actual last seen id.
        for stream_cls in desired_streams:
            key = stream_cls.get_stream_key(self.shard)
            self._streams_to_class[key] = stream_cls
            self._streams_to_last_seen[key] = b"0"

    @property
    def desired_streams(self) -> typing.Sequence[models.protocol.Streamable]:
        return tuple(self._streams_to_class.values())

    def get_last_seen(self, stream: type[models.protocol.Streamable]) -> bytes:
        return self._streams_to_last_seen[stream.get_stream_key(self.shard)]

    async def stream(self) -> collections.abc.AsyncGenerator[models.protocol.Streamable]:
        self._state = ShardStreamReaderState.STREAMING

        while True:
            res = await self.connection.xread(self._streams_to_last_seen, block=0)
            for stream_key, entries in res.items():
                event_cls = self._streams_to_class[stream_key]

                # Realistically if we're here, entries should always be of
                # length >=1. However, there's no way to communicate this to
                # Pyright, so we'll have to make do with this.
                entry_id = None
                for entry_id, entry_data in entries:  # noqa: B007
                    yield event_cls.from_raw(entry_data)

                if entry_id:
                    self._streams_to_last_seen[stream_key] = entry_id

    async def stream_with_dispatcher(
        self,
        dispatcher: collections.abc.Callable[
            [models.protocol.Streamable],
            collections.abc.Coroutine[None, None, None],
        ],
    ) -> None:
        async for entry in self.stream():
            await dispatcher(entry)