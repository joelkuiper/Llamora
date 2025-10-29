from dataclasses import dataclass

@dataclass
class PoolClosedError(Exception):
    message: str = ...

@dataclass
class PoolConnectionAcquireTimeoutError(Exception):
    message: str = ...
