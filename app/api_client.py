from __future__ import annotations

import logging
from uuid import uuid4

import requests

from app.config import load_app_config
from app.models import PostPayload

log = logging.getLogger(__name__)


class ContentApiClient:
    def fetch_post(self) -> PostPayload | None:
        cfg = load_app_config().global_
        if cfg.dry_run:
            token = uuid4().hex[:8]
            return PostPayload(
                post_id=f"dryrun-{token}",
                text="[DRY RUN] Zalo post content",
                images=[],
            )

        request_cfg = cfg.content_api
        resp = requests.request(
            request_cfg.method,
            request_cfg.url,
            headers=request_cfg.headers,
            json=request_cfg.payload or None,
            timeout=cfg.request_timeout_seconds,
        )
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        data = resp.json()
        text = data.get("text")
        if text is None or str(text).strip() == "":
            text = data.get("content")
        images = list(data.get("images") or [])
        payload = PostPayload(
            post_id=str(data.get("post_id") or uuid4().hex),
            text=str(text or "").strip(),
            images=images,
        )
        log.info("Fetched content API payload: text_len=%s image_count=%s", len(payload.text), len(payload.images))
        return payload
