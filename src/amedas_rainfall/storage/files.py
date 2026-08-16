"""途中失敗で既存ファイルを壊さない原子的ファイル書き込み。"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd


def _temporary_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{target.stem}.", suffix=f".tmp{target.suffix}", dir=target.parent
    )
    os.close(fd)
    return Path(name)


@contextmanager
def atomic_output_path(target: Path):
    """一時パスへの書き込み成功時だけtargetへ置換するコンテキスト。"""
    temp = _temporary_path(target)
    try:
        yield temp
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def atomic_write_bytes(target: Path, content: bytes) -> None:
    temp = _temporary_path(target)
    try:
        temp.write_bytes(content)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def atomic_write_json(target: Path, payload: Any) -> None:
    temp = _temporary_path(target)
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def atomic_write_parquet(frame: pd.DataFrame, target: Path, *, index: bool = True) -> None:
    temp = _temporary_path(target)
    try:
        frame.to_parquet(temp, index=index)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
