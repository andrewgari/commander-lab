"""
Pluggable provider registration and retrieval registry for deck analytics.

Allows concrete analytics providers (e.g., EDHRec, Commander Salt) to be
registered, retrieved, and enumerated dynamically at runtime without modifying
core application code.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, List, Optional, Type, Union

from .exceptions import ProviderNotFoundError, ProviderRegistrationError
from .provider import BaseAnalyticsProvider


class AnalyticsProviderRegistry:
    """
    Registry for managing pluggable deck analytics providers.
    """

    def __init__(self) -> None:
        # Maps lowercase provider name to (provider_class_or_instance, is_instance)
        self._providers: Dict[str, Any] = {}
        # Preserves canonical display names
        self._display_names: Dict[str, str] = {}
        # Cache for instances instantiated from registered classes
        self._instances: Dict[str, BaseAnalyticsProvider] = {}

    def register(
        self,
        provider: Optional[Union[Type[BaseAnalyticsProvider], BaseAnalyticsProvider]] = None,
        name: Optional[str] = None,
        overwrite: bool = False,
    ) -> Any:
        """
        Register an analytics provider class or instance.

        Can be called directly:
            registry.register(MyProvider)
            registry.register(MyProvider, name="custom_name")
            registry.register(my_provider_instance)

        Or as a decorator:
            @registry.register
            class MyProvider(BaseAnalyticsProvider): ...

            @registry.register(name="custom_name")
            class MyProvider(BaseAnalyticsProvider): ...

        Args:
            provider: Subclass or instance of BaseAnalyticsProvider.
            name: Optional explicit registration name. If not provided,
                  provider.name or provider.__name__ is used.
            overwrite: If True, allow replacing an existing registered provider.

        Returns:
            The registered provider, or a decorator if called with arguments.
        """
        # Decorator with arguments: @registry.register(name="...", overwrite=...)
        if provider is None:
            def decorator(cls_or_inst: Union[Type[BaseAnalyticsProvider], BaseAnalyticsProvider]) -> Any:
                self.register(cls_or_inst, name=name, overwrite=overwrite)
                return cls_or_inst
            return decorator

        # Validate that provider is a class or instance of BaseAnalyticsProvider
        is_cls = inspect.isclass(provider)
        if is_cls:
            if not issubclass(provider, BaseAnalyticsProvider):
                raise ProviderRegistrationError(
                    f"Class {provider.__name__} must inherit from BaseAnalyticsProvider"
                )
        elif isinstance(provider, BaseAnalyticsProvider):
            pass
        else:
            raise ProviderRegistrationError(
                f"Provider {provider} must be a subclass or instance of BaseAnalyticsProvider"
            )

        # Resolve provider name
        resolved_name = name
        if not resolved_name:
            if not is_cls:
                resolved_name = getattr(provider, "name", None)
            else:
                # If class has a class-level name attribute
                cls_name_attr = getattr(provider, "name", None)
                if isinstance(cls_name_attr, str):
                    resolved_name = cls_name_attr
                else:
                    resolved_name = provider.__name__.lower()
                    if resolved_name.endswith("provider"):
                        resolved_name = resolved_name[:-8]

        if not resolved_name or not isinstance(resolved_name, str) or not resolved_name.strip():
            raise ProviderRegistrationError(
                "Could not determine a valid non-empty string name for provider"
            )

        canonical_name = resolved_name.strip()
        key = canonical_name.lower()

        if key in self._providers and not overwrite:
            raise ProviderRegistrationError(
                f"Provider '{canonical_name}' is already registered. "
                "Use overwrite=True to replace it."
            )

        self._providers[key] = provider
        self._display_names[key] = canonical_name
        # Clear any cached instance for this key on re-registration
        if key in self._instances:
            del self._instances[key]

        return provider

    def get(self, name: str) -> BaseAnalyticsProvider:
        """
        Retrieve an instantiated analytics provider by name.

        If a provider class was registered, an instance will be instantiated
        (and cached for future calls).

        Args:
            name: Case-insensitive name of the provider.

        Returns:
            An instantiated BaseAnalyticsProvider.

        Raises:
            ProviderNotFoundError: If provider name is not registered.
        """
        if not isinstance(name, str):
            raise ProviderNotFoundError(f"Provider name must be a string, got {type(name).__name__}")

        key = name.strip().lower()
        if key not in self._providers:
            available = ", ".join(repr(n) for n in sorted(self._display_names.values()))
            raise ProviderNotFoundError(
                f"Analytics provider '{name}' is not registered. Available providers: [{available}]"
            )

        if key in self._instances:
            return self._instances[key]

        provider_entry = self._providers[key]
        if inspect.isclass(provider_entry):
            try:
                instance = provider_entry()
            except Exception as e:
                raise ProviderRegistrationError(
                    f"Failed to instantiate provider class '{provider_entry.__name__}': {e}"
                ) from e
            self._instances[key] = instance
            return instance
        else:
            self._instances[key] = provider_entry
            return provider_entry

    def list_providers(self) -> List[str]:
        """
        List all registered provider names (sorted).

        Returns:
            List of registered provider names.
        """
        return sorted(self._display_names.values())

    def unregister(self, name: str) -> bool:
        """
        Unregister a provider by name.

        Args:
            name: Provider name to remove.

        Returns:
            True if removed, False if it was not registered.
        """
        if not isinstance(name, str):
            return False
        key = name.strip().lower()
        if key in self._providers:
            del self._providers[key]
            del self._display_names[key]
            if key in self._instances:
                del self._instances[key]
            return True
        return False

    def clear(self) -> None:
        """Remove all registered providers and cached instances."""
        self._providers.clear()
        self._display_names.clear()
        self._instances.clear()


# Default global registry singleton
default_registry = AnalyticsProviderRegistry()


def register_provider(
    provider: Optional[Union[Type[BaseAnalyticsProvider], BaseAnalyticsProvider]] = None,
    name: Optional[str] = None,
    overwrite: bool = False,
) -> Any:
    """Register a provider on the default global registry."""
    return default_registry.register(provider=provider, name=name, overwrite=overwrite)


def get_provider(name: str) -> BaseAnalyticsProvider:
    """Retrieve an instantiated provider from the default global registry."""
    return default_registry.get(name)


def list_providers() -> List[str]:
    """List all registered provider names in the default global registry."""
    return default_registry.list_providers()


def unregister_provider(name: str) -> bool:
    """Unregister a provider from the default global registry."""
    return default_registry.unregister(name)


def clear_registry() -> None:
    """Clear all registered providers from the default global registry."""
    default_registry.clear()
