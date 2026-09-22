"""Shared resource ownership for all local server entry modes."""
import json
from pathlib import Path
import time


class EventLog:
    """Line-buffered metadata sink; preserves each legacy mode's JSON policy."""
    def __init__(self,path,*,exclusive=False,timestamps=False,ensure_ascii=False):
        self.path=Path(path);self.exclusive=exclusive;self.timestamps=timestamps
        self.ensure_ascii=ensure_ascii;self.file=None

    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('x' if self.exclusive else 'a',encoding='utf-8',buffering=1)
        return self

    def __call__(self,row):
        if self.timestamps:row={'timestamp':time.time(),**row}
        try:self.file.write(json.dumps(row,ensure_ascii=self.ensure_ascii)+'\n')
        except OSError:pass  #telemetry failure must not terminate a game session

    def __exit__(self,*exc):
        if self.file:self.file.close()


def own_services(stack,services):
    """Register BEFORE start, keep requested close order, close all on errors.

    AsyncExitStack continues remaining cleanup callbacks even if one close
    fails. Constructor/store ownership belongs to the composing runner.
    """
    for service in reversed(services):stack.push_async_callback(service.close)


async def start_services(services):
    for service in services:await service.start()
