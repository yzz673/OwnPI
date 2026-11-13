"""Shared runtime memory map and consumption contracts."""

from ownpi.runtime.memory.map_builder import MemoryMapBuilder, MemoryMapCallScope
from ownpi.runtime.memory.map_contracts import (
    MemoryAuthority,
    MemoryDomain,
    MemoryMap,
    MemoryMapIdentityReceipt,
    MemoryMapSection,
    MemoryMapStatus,
    MemoryMutability,
    render_memory_map,
)
from ownpi.runtime.memory.receipts import (
    MemoryConsumptionOperation,
    MemoryConsumptionReceipt,
    MemoryConsumptionStatus,
    memory_consumption_context_effects,
    with_memory_consumption_receipt,
)

__all__ = [
    'MemoryAuthority',
    'MemoryConsumptionOperation',
    'MemoryConsumptionReceipt',
    'MemoryConsumptionStatus',
    'MemoryDomain',
    'MemoryMap',
    'MemoryMapBuilder',
    'MemoryMapCallScope',
    'MemoryMapIdentityReceipt',
    'MemoryMapSection',
    'MemoryMapStatus',
    'MemoryMutability',
    'memory_consumption_context_effects',
    'render_memory_map',
    'with_memory_consumption_receipt',
]
