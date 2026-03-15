from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests

from app.config import load_app_config


class TempStorage:
    def __init__(self) -> None:
        self.cfg = load_app_config().global_

    def prepare_job_dir(self, account_id: str, post_id: str) -> Path:
        path = Path(self.cfg.local_tmp_root) / account_id / post_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def download_images(self, urls: list[str], target_dir: Path) -> list[Path]:
        paths: list[Path] = []
        for idx, url in enumerate(urls, start=1):
            parsed = urlparse(url)
            suffix = Path(parsed.path).suffix or ".jpg"
            local_path = target_dir / f"image_{idx}{suffix}"
            with requests.get(url, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                with local_path.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1024 * 128):
                        if chunk:
                            fh.write(chunk)
            paths.append(local_path)
        return paths

    def cleanup_dir(self, target_dir: Path) -> None:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
