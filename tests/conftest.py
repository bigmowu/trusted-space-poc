import os
import tempfile


TEST_ROOT = tempfile.mkdtemp(prefix="trusted-space-tests-")
os.environ["TRUSTED_SPACE_DATA_DIR"] = os.path.join(TEST_ROOT, "data")
os.environ["TRUSTED_SPACE_DB"] = os.path.join(TEST_ROOT, "data", "test.db")
os.environ["TRUSTED_SPACE_EXPORTS"] = os.path.join(TEST_ROOT, "exports")
os.environ["TRUSTED_SPACE_LOGS"] = os.path.join(TEST_ROOT, "logs")
os.environ["SANDBOX_BACKEND"] = "mock"

