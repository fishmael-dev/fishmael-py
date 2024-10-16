import asyncio
import dataclasses
import inspect
import types
import typing

from fishmael.events import base as events_base
from fishmael.internal import async_utils, class_utils

if typing.TYPE_CHECKING:
    import collections.abc

ListenerMap = dict[
    type[events_base.Event],
    list[events_base.EventCallbackT[events_base.Event]],
]

@dataclasses.dataclass
class EventManager:
    _listeners: ListenerMap = dataclasses.field(default_factory=dict, init=False)

    def subscribe(
        self,
        event_type: type[events_base.EventT],
        callback: events_base.EventCallbackT[events_base.EventT],
    ) -> None:
        try:
            self._listeners[event_type].append(callback)  # pyright: ignore[reportArgumentType]
        except KeyError:
            self._listeners[event_type] = [callback]  # pyright: ignore[reportArgumentType]

    def unsubscribe(
        self,
        event_type: type[events_base.EventT],
        callback: events_base.EventCallbackT[events_base.EventT],
    ) -> None:
        listeners = self._listeners.get(event_type)
        if not listeners:
            return

        listeners.remove(callback)  # pyright: ignore[reportArgumentType]
        if not listeners:
            del self._listeners[event_type]

    def dispatch(self, event: events_base.Event) -> asyncio.Future[typing.Any]:
        tasks: list[collections.abc.Coroutine[None, None, None]] = []

        for cls in event.dispatches:
            for callback in self._listeners.get(cls, ()):
                tasks.append(callback(event))

            # TODO: waiters

        return asyncio.gather(*tasks) if tasks else async_utils.create_completed_future()

    def listen(
        self,
        *event_types: type[events_base.EventT],
    ) -> typing.Callable[
        [events_base.EventCallbackT[events_base.EventT]],
        events_base.EventCallbackT[events_base.EventT],
    ]:
        def wrapper(
            callback: events_base.EventCallbackT[events_base.EventT],
        ) -> events_base.EventCallbackT[events_base.EventT]:
            signature = inspect.signature(callback)
            parameters = signature.parameters.values()
            event_param = next(iter(parameters))
            annotation = event_param.annotation

            if annotation is inspect.Parameter.empty:
                if event_types:
                    resolved_types = event_types

                else:
                    msg = (
                        "Please provide the event type either as an argument to"
                        " the decorator or as a type hint."
                    )
                    raise TypeError(msg)

            else:
                if typing.get_origin(annotation) in (typing.Union, types.UnionType):
                    resolved_types = {
                        class_utils.strip_generic(ann) for ann in typing.get_args(annotation)
                    }
                else:
                    resolved_types = {class_utils.strip_generic(annotation)}

                if event_types and resolved_types != set(event_types):
                    msg = (
                        "Please make sure the event types provided to the"
                        " decorator match those provided as a typehint."
                    )
                    raise TypeError(msg)

            for event_type in resolved_types:
                self.subscribe(event_type, callback)

            return callback

        return wrapper
