"""A weak-reference registry assigning asyncio.Lock objects by string key, a shared primitive for in-process exclusion."""

from __future__ import annotations

import asyncio
import weakref


class KeyedLocks:
    """Requests with the same key share a lock; weak references allow automatic collection when no coroutine holds or awaits it.

    Use for critical sections serialized by key, such as lazy session creation or new-session creation with the same idempotency key.
    The caller holds a strong reference during ``async with lock_for(key)``, keeping the lock alive. After the critical section exits
    and no waiters remain, it disappears from the registry automatically rather than permanently retaining memory for every key."""

    def __init__(self) -> None:
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def lock_for(self, key: str) -> asyncio.Lock:
        """Get the lock for this key, creating it if absent; concurrent callers receive the same instance."""
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def discard(self, key: str) -> None:
        """Explicitly remove the lock reference for a key; waiters already holding the lock object are unaffected."""
        self._locks.pop(key, None)

    def __len__(self) -> int:
        return len(self._locks)

    def __contains__(self, key: str) -> bool:
        return key in self._locks
