from __future__ import annotations

import json
from typing import Protocol
from urllib.parse import urlencode

from .models import DeckBackendRequest


class DeckBackendAdapter(Protocol):
    def build_urls(
        self,
        request: DeckBackendRequest,
        *,
        site_bases: list[str],
    ) -> list[str]: ...


class MoesekaiDeckScreenshotAdapter:
    def build_urls(
        self,
        request: DeckBackendRequest,
        *,
        site_bases: list[str],
    ) -> list[str]:
        query = self.build_query(request)
        encoded = urlencode(query)
        return [
            f"{base.rstrip('/')}/deck-recommend/?{encoded}"
            for base in site_bases
        ]

    def build_query(self, request: DeckBackendRequest) -> dict[str, str]:
        query = {
            "mode": "screenshot",
            "userId": request.game_id,
            "server": request.server,
            "deckMode": request.mode,
        }
        if request.mode == "event":
            query["eventId"] = str(request.event_id or 0)
            query["musicId"] = str(request.music_id or 0)
            query["difficulty"] = str(request.difficulty or "")
            query["liveType"] = str(request.live_type or "")
        elif request.mode == "mysekai":
            query["eventId"] = str(request.event_id or 0)
        elif request.mode == "strongest":
            query["musicId"] = str(request.music_id or 0)
            query["difficulty"] = str(request.difficulty or "")
            query["liveType"] = str(request.live_type or "")
            query["strongestTarget"] = str(request.strongest_target or "power")
        elif request.mode == "challenge":
            query["characterId"] = str(request.character_id or 0)
            query["musicId"] = str(request.music_id or 0)
            query["difficulty"] = str(request.difficulty or "")
        elif request.mode == "custom":
            query["musicId"] = str(request.music_id or 0)
            query["difficulty"] = str(request.difficulty or "")
            query["liveType"] = str(request.live_type or "")
            if request.custom_attr:
                query["customAttr"] = request.custom_attr
            if request.custom_unit:
                query["customUnit"] = request.custom_unit
            if request.custom_character_ids:
                query["customCharacterIds"] = ",".join(
                    str(character_id) for character_id in request.custom_character_ids
                )
            if request.custom_character_units:
                query["customCharacterUnits"] = json.dumps(
                    {
                        str(character_id): unit
                        for character_id, unit in sorted(
                            request.custom_character_units.items()
                        )
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
        return query


moesekai_deck_screenshot_adapter = MoesekaiDeckScreenshotAdapter()
