import sys
import types

# Stub dotenv.load_dotenv so configuration imports succeed without the package
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None))

# Provide minimal aiohttp stubs used by service modules
class _ClientTimeout:
    def __init__(self, total=None, connect=None):
        self.total = total
        self.connect = connect

class _ClientSession:
    async def close(self):
        pass

sys.modules.setdefault(
    "aiohttp", types.SimpleNamespace(ClientSession=_ClientSession, ClientTimeout=_ClientTimeout)
)
