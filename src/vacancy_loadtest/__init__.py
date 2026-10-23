"""Locust load test for the Cyrex gRPC vacancy service."""

import os

# gRPC reads GRPC_DNS_RESOLVER once, when its C core initialises on the first `import grpc`,
# so this has to happen here, before any submodule imports grpc. The bundled c-ares resolver
# timed out intermittently on the load generator during development ("Timeout while
# contacting DNS servers"), the OS resolver did not; a DNS hiccup on the client must not be
# reported as a server error. Export GRPC_DNS_RESOLVER yourself to override.
os.environ.setdefault("GRPC_DNS_RESOLVER", "native")

__version__ = "0.1.0"
