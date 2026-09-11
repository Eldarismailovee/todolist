"""Позиции для drag-and-drop.

Элемент вставляется между соседями значением посередине, поэтому перемещение
меняет одну строку, а не переписывает весь список. Когда зазор между соседями
исчерпан (float перестаёт различать середину), список перенумеровывается.
"""

STEP = 1024.0
# Ниже этого зазора середина уже не даёт различимого значения.
MIN_GAP = 1e-6


def position_between(previous: float | None, following: float | None) -> float | None:
    """Позиция между соседями; None — если требуется перенумерация."""
    if previous is None and following is None:
        return STEP
    if previous is None:
        return following - STEP
    if following is None:
        return previous + STEP
    if following - previous < MIN_GAP:
        return None
    return (previous + following) / 2


def renumber(count: int) -> list[float]:
    """Равномерные позиции после перенумерации."""
    return [STEP * (index + 1) for index in range(count)]
