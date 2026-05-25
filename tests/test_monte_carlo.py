import pytest
import os
import sys

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def test_monte_carlo_importable():
    """monte_carlo module must import without crashing."""
    from monte_carlo import SimulationEngine
    assert SimulationEngine is not None


def test_simulation_engine_loads_weights_on_init():
    """Weights should be loaded during __init__, not at module level."""
    from monte_carlo import SimulationEngine
    engine = SimulationEngine()
    assert engine.time_weights is not None
    assert "Morning" in engine.time_weights
    assert "Evening" in engine.time_weights
