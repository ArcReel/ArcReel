"""按字符串键分配 asyncio.Lock 的弱引用注册表（进程内互斥的共享原语）。"""

from __future__ import annotations

import asyncio
import weakref


class KeyedLocks:
    """同键请求共享同一把锁；无协程持有/等待时锁对象随弱引用自动回收。

    适用于按 key 串行化的临界区（如会话懒生成、同幂等键的新会话创建）：
    调用方在 ``async with lock_for(key)`` 期间持有强引用，锁存活；临界区
    退出且再无等待者后自动从注册表消失，不为每个键永久驻留内存。
    """

    def __init__(self) -> None:
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def lock_for(self, key: str) -> asyncio.Lock:
        """取该键的锁，不存在时创建；并发调用者拿到同一实例。"""
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def discard(self, key: str) -> None:
        """主动清除键的锁引用；已持有锁对象的等待者不受影响。"""
        self._locks.pop(key, None)

    def __len__(self) -> int:
        return len(self._locks)

    def __contains__(self, key: str) -> bool:
        return key in self._locks
