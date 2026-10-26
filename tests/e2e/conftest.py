"""
Pytest configuration for Playwright E2E tests.

Provides fixtures for browser context and page management.
The pytest-playwright plugin already provides --base-url option.
"""
import pytest


# Default base URL for local development
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "e2e: mark test as end-to-end test"
    )


@pytest.fixture(scope="session")
def base_url(request) -> str:
    """Get the base URL for the application under test.
    
    Uses the --base-url command line option from pytest-playwright.
    Defaults to http://localhost:8000.
    """
    return request.config.getoption("--base-url", default="http://localhost:8000")
