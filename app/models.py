from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PostPayload:
    post_id: str
    text: str
    images: list[str] = field(default_factory=list)
