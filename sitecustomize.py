"""Import project provided stubs early in the Python startup process.

Having ``fastapi`` available before the test suite executes prevents individual
tests from installing incomplete stand-ins that would otherwise affect later
tests.  The real FastAPI package is not required; the project ships with a
lightweight stub that is sufficient for unit testing.
"""

import fastapi  # noqa: F401 - imported for side effects
import fastapi.testclient  # noqa: F401

