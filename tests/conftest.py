"""Root test configuration for shared test fixtures."""

# Re-export Playspace integration fixtures so top-level tests can request them.
from tests.products.playspace.conftest import (  # noqa: F401
    PlayspaceSeedSnapshot,
    playspace_client,
    playspace_seed_snapshot,
    playspace_test_session_factory,
)
