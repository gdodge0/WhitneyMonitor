import os
import pickle
import aiofiles
import asyncio
from collections.abc import MutableMapping


class AsyncAutoSavingDict(MutableMapping):
    def __init__(self, data_dir="data/whitney", data_file="previous_data.p"):
        self.data_dir = data_dir
        self.data_file = data_file
        self.full_path = os.path.join(data_dir, data_file)
        self._store = {}
        self._lock = asyncio.Lock()

    async def _ensure_dir(self):
        os.makedirs(self.data_dir, exist_ok=True)

    async def load(self):
        await self._ensure_dir()

        if os.path.exists(self.full_path):
            try:
                async with aiofiles.open(self.full_path, "rb") as f:
                    content = await f.read()
                    self._store = pickle.loads(content)
            except (pickle.UnpicklingError, EOFError) as e:
                print(f"Warning: Failed to load data: {e}")
                self._store = {}

    async def save(self):
        async with self._lock:
            await self._ensure_dir()
            content = pickle.dumps(self._store)
            async with aiofiles.open(self.full_path, "wb") as f:
                await f.write(content)

    # Dictionary interface methods
    def __getitem__(self, key):
        return self._store[key]

    def __setitem__(self, key, value):
        self._store[key] = value
        asyncio.create_task(self.save())

    def __delitem__(self, key):
        del self._store[key]
        asyncio.create_task(self.save())

    def __iter__(self):
        return iter(self._store)

    def __len__(self):
        return len(self._store)

    def __repr__(self):
        return repr(self._store)
