from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

TZ_NAME = os.environ.get('NEWS_TIMEZONE', 'Asia/Bangkok')
TZ = ZoneInfo(TZ_NAME)


@dataclass(frozen=True)
class RuntimeConfig:
    now: datetime
    run_date: str
    run_hhmm: str
    hour_24: int
    spoken_hour_vi: str
    iso_local: str
    tz_name: str


def get_runtime() -> RuntimeConfig:
    now = datetime.now(TZ)
    run_date = os.environ.get('RUN_DATE') or now.strftime('%Y-%m-%d')
    run_hhmm = os.environ.get('RUN_HHMM') or now.strftime('%H%M')
    hour_24 = int(os.environ.get('RUN_HOUR_24') or now.strftime('%H'))
    spoken_hour_vi = os.environ.get('RUN_HOUR') or f'{hour_24} giờ'
    iso_local = f'{run_date} {run_hhmm[:2]}:{run_hhmm[2:]} {TZ_NAME}'
    return RuntimeConfig(
        now=now,
        run_date=run_date,
        run_hhmm=run_hhmm,
        hour_24=hour_24,
        spoken_hour_vi=spoken_hour_vi,
        iso_local=iso_local,
        tz_name=TZ_NAME,
    )


def world_video_filename(run_date: str, run_hhmm: str) -> str:
    return f'world-news-vertical-vi-{run_date}-{run_hhmm}.mp4'


def vn_video_filename(run_date: str, run_hhmm: str) -> str:
    return f'vietnam-news-{run_date}-{run_hhmm}.mp4'
