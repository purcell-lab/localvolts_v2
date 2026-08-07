"""Root pytest configuration.

Declaring pytest_plugins here rather than in tests/conftest.py because pytest
only permits the pytest_plugins global in the rootdir conftest. Having a
conftest at the repository root also puts the root on sys.path, which is what
makes the custom_components package importable from the test modules.
"""

pytest_plugins = "pytest_homeassistant_custom_component"
