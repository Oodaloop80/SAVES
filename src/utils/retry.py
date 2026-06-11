import asyncio
import functools
import time


def with_retry(attempts=3, base_delay=30, exceptions=(Exception,)):
    def decorator(fn):
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                last_exc = None
                for attempt in range(attempts):
                    try:
                        return await fn(*args, **kwargs)
                    except exceptions as e:
                        last_exc = e
                        if attempt < attempts - 1:
                            await asyncio.sleep(base_delay * (2 ** attempt))
                raise last_exc
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                last_exc = None
                for attempt in range(attempts):
                    try:
                        return fn(*args, **kwargs)
                    except exceptions as e:
                        last_exc = e
                        if attempt < attempts - 1:
                            time.sleep(base_delay * (2 ** attempt))
                raise last_exc
            return sync_wrapper
    return decorator
