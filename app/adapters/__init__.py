"""Machine Intelligence adapters. Observations only; no canonical writes."""

from app.adapters.base import ADAPTER_VERSION, AdapterError, PackageRejected

__all__ = [
    "ADAPTER_VERSION",
    "AdapterError",
    "PackageRejected",
]
