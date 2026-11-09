"""Discover trusted local modules or installed entry points; core stays unchanged."""
import importlib
import logging
from importlib.metadata import entry_points
import pkgutil

from . import __path__ as adapter_path

log = logging.getLogger(__name__)


def discover_adapters():
    """Load trusted installed code; adding an adapter needs no pipeline branches."""
    adapters = {}

    def register(adapter):
        if not getattr(adapter, "name", "") or adapter.name in adapters:
            raise ValueError("Adapter names must be nonempty and unique")
        adapters[adapter.name] = adapter
        log.debug("adapter_registered adapter=%s", adapter.name)

    for module_info in pkgutil.iter_modules(adapter_path):
        if module_info.name.startswith("_") or module_info.name in {"base", "registry"}:
            continue
        module = importlib.import_module(f"jobscrap.adapters.{module_info.name}")
        if hasattr(module, "ADAPTER"):
            register(module.ADAPTER)
    # Entry points let a separately installed package provide the same contract.
    for entry in entry_points(group="jobscrap.adapters"):
        register(entry.load()())
    log.debug("adapter_discovery_finished count=%s", len(adapters))
    return adapters
