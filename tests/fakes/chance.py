from __future__ import annotations


class FixedChance:
    """Stands in for random.Random where only random() is used (Backoff's jitter): it gives these numbers in turn,
    and the last one over and over, so a test knows each jittered wait."""

    def __init__(self, *numbers: float):
        self.numbers = list(numbers)

    def random(self) -> float:
        return self.numbers.pop(0) if len(self.numbers) > 1 else self.numbers[0]
