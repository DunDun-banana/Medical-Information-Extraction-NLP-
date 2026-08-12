from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Generic, Iterable, Iterator, TypeVar
import unicodedata

T = TypeVar("T")


@dataclass
class _Node(Generic[T]):
    children: dict[str, int] = field(default_factory=dict)
    failure: int = 0
    outputs: list[tuple[int, T, str]] = field(default_factory=list)


class AhoDictionaryMatcher(Generic[T]):
    """Unicode-safe Aho-Corasick matcher with exact character offsets.

    Python's lower() preserves string length for Vietnamese clinical text, so
    offsets in the lowered scan map directly to the original text. The matcher
    scans a document once instead of scanning it once per dictionary entry.
    """

    def __init__(self, entries: Iterable[tuple[str, T]]):
        self.nodes: list[_Node[T]] = [_Node()]
        count = 0
        for surface, payload in entries:
            normalized = surface.lower()
            if not normalized:
                continue
            state = 0
            for char in normalized:
                next_state = self.nodes[state].children.get(char)
                if next_state is None:
                    next_state = len(self.nodes)
                    self.nodes[state].children[char] = next_state
                    self.nodes.append(_Node())
                state = next_state
            self.nodes[state].outputs.append((len(normalized), payload, surface))
            count += 1
        self.entry_count = count
        self._build_failures()

    def _build_failures(self) -> None:
        queue: deque[int] = deque()
        for child in self.nodes[0].children.values():
            self.nodes[child].failure = 0
            queue.append(child)
        while queue:
            state = queue.popleft()
            for char, child in self.nodes[state].children.items():
                queue.append(child)
                failure = self.nodes[state].failure
                while failure and char not in self.nodes[failure].children:
                    failure = self.nodes[failure].failure
                self.nodes[child].failure = self.nodes[failure].children.get(char, 0)
                inherited = self.nodes[self.nodes[child].failure].outputs
                if inherited:
                    self.nodes[child].outputs.extend(inherited)

    @staticmethod
    def _is_word(char: str) -> bool:
        # Vietnamese corpora often contain decomposed diacritics (e.g. "học"
        # is h-o-COMBINING_DOT-c). Combining marks must count as part of the
        # surrounding word; otherwise short aliases such as "ho" are falsely
        # matched inside "học"/"mô bệnh học".
        category = unicodedata.category(char)
        return char == "_" or char.isalnum() or category.startswith("M")

    @classmethod
    def _word_boundary(cls, text: str, start: int, end: int, surface: str) -> bool:
        if surface and cls._is_word(surface[0]):
            if start > 0 and cls._is_word(text[start - 1]):
                return False
        if surface and cls._is_word(surface[-1]):
            if end < len(text) and cls._is_word(text[end]):
                return False
        return True

    def finditer(
        self,
        text: str,
        *,
        require_word_boundary: bool = True,
    ) -> Iterator[tuple[int, int, T]]:
        lowered = text.lower()
        state = 0
        for index, char in enumerate(lowered):
            while state and char not in self.nodes[state].children:
                state = self.nodes[state].failure
            state = self.nodes[state].children.get(char, 0)
            for length, payload, surface in self.nodes[state].outputs:
                end = index + 1
                start = end - length
                if require_word_boundary and not self._word_boundary(
                    text, start, end, surface
                ):
                    continue
                yield start, end, payload
