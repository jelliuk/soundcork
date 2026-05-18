"""
conftest.py for Soundcork Bose API Compliance Tests
====================================================

Provides shared pytest configuration. The main fixtures (data_dir, client,
acc, tc) are all defined inside test_bose_api_compliance.py itself as
module-scoped fixtures so they spin up the datastore and FastAPI test client
once per test module — keeping the suite fast.

If SOUNDCORK_MGMT_PASSWORD is set in the environment, it will be used for
the mgmt API Basic Auth tests; otherwise the in-test mock value is used.
"""
import os
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: marks tests that require a live TuneIn/network connection",
    )
    config.addinivalue_line(
        "markers",
        "schema: marks tests that validate OpenAPI schema compliance",
    )
    config.addinivalue_line(
        "markers",
        "sequence: marks tests that simulate speaker call sequences",
    )
