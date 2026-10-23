"""gRPC ↔ Locust glue.

* :class:`LocustInterceptor` is a client-side channel interceptor that reports **every** RPC
  (unary and server-streaming) to Locust's ``request`` event, including the gRPC status code
  and details of failed calls. Locust then does the rest: response-time percentiles, RPS,
  failure table, charts, CSV/HTML reports.
* :class:`GrpcUser` is a Locust ``User`` that owns one intercepted channel.

The unary part follows the gRPC example in the Locust documentation (MIT licensed); the
streaming wrapper and channel teardown are additions.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

import grpc
import grpc.experimental.gevent as grpc_gevent
from locust import User
from locust.env import Environment
from locust.exception import LocustError

log = logging.getLogger(__name__)

# Locust runs every user in a gevent greenlet. Without this patch a blocking call into
# gRPC's C core would freeze the whole process (and every other user) until it returns.
grpc_gevent.init_gevent()

#: ``request_type`` values shown in Locust's statistics table
REQUEST_TYPE_UNARY = "gRPC"
REQUEST_TYPE_STREAM = "gRPC-stream"


def rpc_name(method: str) -> str:
    """``/pb.VacancyService/CreateVacancy`` → ``pb.VacancyService/CreateVacancy``."""
    return method.lstrip("/")


class RpcFailure(Exception):
    """What Locust records for a failed RPC: a one-line ``CODE: details`` message.

    ``grpc.RpcError``'s own string form is a multi-line block that includes a debug string
    with timestamps and addresses, which would spread identical failures over many rows of
    Locust's failure table. The original error stays available as ``error``.
    """

    def __init__(self, error: grpc.RpcError) -> None:
        self.error = error
        self.code = error.code()
        self.details = error.details()
        super().__init__(f"{self.code.name}: {self.details}")

    def __repr__(self) -> str:  # Locust shows repr() in its failure table
        return str(self)


class LocustInterceptor(grpc.UnaryUnaryClientInterceptor, grpc.UnaryStreamClientInterceptor):
    """Report each RPC on the channel to ``environment.events.request``."""

    def __init__(self, environment: Environment) -> None:
        self._environment = environment

    def _fire(
        self,
        *,
        request_type: str,
        method: str,
        start: float,
        response_length: int,
        exception: BaseException | None,
        response: Any = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self._environment.events.request.fire(
            request_type=request_type,
            name=rpc_name(method),
            response_time=(time.perf_counter() - start) * 1000.0,
            response_length=response_length,
            response=response,
            context=context or {},
            exception=exception,
        )

    # -- unary → unary -----------------------------------------------------------------
    def intercept_unary_unary(
        self,
        continuation: Callable[..., Any],
        client_call_details: grpc.ClientCallDetails,
        request: Any,
    ) -> Any:
        start = time.perf_counter()
        response_length = 0
        exception: BaseException | None = None
        # `continuation` never raises: on failure it returns the RpcError itself, whose
        # .result() re-raises it. We must hand that object back so the caller sees the error.
        call = continuation(client_call_details, request)
        try:
            response_length = call.result().ByteSize()
        except grpc.RpcError as exc:
            exception = RpcFailure(exc)
        except Exception as exc:  # defensive: anything else .result() might raise
            exception = exc
        self._fire(
            request_type=REQUEST_TYPE_UNARY,
            method=client_call_details.method,
            start=start,
            response_length=response_length,
            exception=exception,
            response=call,
        )
        return call

    # -- unary → stream ----------------------------------------------------------------
    def intercept_unary_stream(
        self,
        continuation: Callable[..., Any],
        client_call_details: grpc.ClientCallDetails,
        request: Any,
    ) -> Any:
        start = time.perf_counter()
        try:
            call = continuation(client_call_details, request)
        except grpc.RpcError as exc:  # pragma: no cover
            # streaming errors normally surface on iteration, not here
            self._fire(
                request_type=REQUEST_TYPE_STREAM,
                method=client_call_details.method,
                start=start,
                response_length=0,
                exception=RpcFailure(exc),
            )
            raise

        def report(response_length: int, items: int, exception: BaseException | None) -> None:
            self._fire(
                request_type=REQUEST_TYPE_STREAM,
                method=client_call_details.method,
                start=start,
                response_length=response_length,
                exception=exception,
                response=call,
                context={"items": items},
            )

        return TimedStream(call, report)


class TimedStream:
    """Wrap a server-streaming call; report it once, when it is fully consumed or fails.

    The reported response time is the time until the **last** message arrived, i.e. what a
    client waiting for the complete list experiences. A stream that is abandoned half-way is
    not reported.
    """

    def __init__(self, call: Any, report: Callable[[int, int, BaseException | None], None]) -> None:
        self._call = call
        self._report = report
        self._iterator: Iterator[Any] | None = None
        self._bytes = 0
        self._items = 0
        self._reported = False

    def __iter__(self) -> TimedStream:
        return self

    def __next__(self) -> Any:
        if self._iterator is None:
            self._iterator = iter(self._call)
        try:
            message = next(self._iterator)
        except StopIteration:
            self._finish(None)
            raise
        except grpc.RpcError as exc:
            self._finish(RpcFailure(exc))
            raise
        self._items += 1
        self._bytes += message.ByteSize()
        return message

    def _finish(self, exception: BaseException | None) -> None:
        if not self._reported:
            self._reported = True
            self._report(self._bytes, self._items, exception)

    def __getattr__(self, name: str) -> Any:  # grpc.Call API: code(), details(), cancel(), …
        return getattr(self._call, name)


class GrpcUser(User):
    """A Locust user with one intercepted, plaintext gRPC channel (the task's server is plain TCP).

    Subclasses set ``host`` (``host:port``) and create their stubs on ``self.channel``.
    """

    abstract = True

    def __init__(self, environment: Environment) -> None:
        super().__init__(environment)
        # --host / the web UI set environment.host; the runner also copies it onto the class.
        self.host = getattr(environment, "host", None) or self.host
        if not self.host:
            raise LocustError(
                "GrpcUser needs a host (set the `host` class attribute or pass --host host:port)"
            )
        self._raw_channel = grpc.insecure_channel(self.host)
        self.channel = grpc.intercept_channel(self._raw_channel, LocustInterceptor(environment))

    def on_stop(self) -> None:
        self._raw_channel.close()
