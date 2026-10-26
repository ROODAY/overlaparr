"""SQLAlchemy models for runtime state (config lives in YAML, not here)."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UtcDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes; SQLite drops tzinfo so restore it on read."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use timezone-aware UTC")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {
        datetime: UtcDateTime(),
        dict[str, Any]: JSON,
        list[int]: JSON,
    }


class AppState(Base):
    __tablename__ = "app_state"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String)


class Server(Base):
    __tablename__ = "servers"
    machine_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    # online | degraded | offline | stale | partial
    reachability: Mapped[str] = mapped_column(default="offline")
    last_indexed_at: Mapped[datetime | None]
    last_attempt_at: Mapped[datetime | None]
    movie_count: Mapped[int] = mapped_column(default=0)
    show_count: Mapped[int] = mapped_column(default=0)
    unmatchable_count: Mapped[int] = mapped_column(default=0)
    is_new: Mapped[bool] = mapped_column(default=True)
    is_home: Mapped[bool] = mapped_column(default=False)


class IndexItem(Base):
    __tablename__ = "index_items"
    __table_args__ = (
        UniqueConstraint("server_id", "rating_key"),
        Index("ix_index_items_server_tmdb", "server_id", "tmdb"),
        Index("ix_index_items_server_tvdb", "server_id", "tvdb"),
        Index("ix_index_items_server_imdb", "server_id", "imdb"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[str] = mapped_column(ForeignKey("servers.machine_id", ondelete="CASCADE"))
    kind: Mapped[str]  # movie | show
    rating_key: Mapped[str]
    title: Mapped[str]
    year: Mapped[int | None]
    tmdb: Mapped[int | None]
    tvdb: Mapped[int | None]
    imdb: Mapped[str | None]
    resolution: Mapped[int | None]
    hdr: Mapped[bool] = mapped_column(default=False)
    codec: Mapped[str | None]
    edition: Mapped[str | None]
    size: Mapped[int | None]


class IndexEpisode(Base):
    __tablename__ = "index_episodes"
    __table_args__ = (Index("ix_index_episodes_server_show", "server_id", "show_rating_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[str] = mapped_column(ForeignKey("servers.machine_id", ondelete="CASCADE"))
    show_rating_key: Mapped[str]
    season: Mapped[int]
    episode: Mapped[int]
    tvdb_episode_id: Mapped[int | None]
    resolution: Mapped[int | None]
    hdr: Mapped[bool] = mapped_column(default=False)
    codec: Mapped[str | None]
    size: Mapped[int | None]
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)


class TrackedItem(Base):
    __tablename__ = "tracked_items"
    __table_args__ = (UniqueConstraint("instance_id", "arr_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[str]
    arr_id: Mapped[int]
    kind: Mapped[str]  # movie | series
    title: Mapped[str]
    year: Mapped[int | None]
    tmdb: Mapped[int | None]
    tvdb: Mapped[int | None]
    imdb: Mapped[str | None]
    monitored: Mapped[bool] = mapped_column(default=False)
    has_file: Mapped[bool] = mapped_column(default=False)
    file_size: Mapped[int | None]
    resolution: Mapped[int | None]
    edition: Mapped[str | None]
    cutoff_resolution: Mapped[int | None]
    quality_profile_id: Mapped[int | None]
    tag_ids: Mapped[list[int]] = mapped_column(default=list)
    poster_url: Mapped[str | None]
    title_slug: Mapped[str | None]
    seen_scan_id: Mapped[int]


class TrackedEpisode(Base):
    __tablename__ = "tracked_episodes"
    __table_args__ = (UniqueConstraint("instance_id", "episode_arr_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[str]
    series_arr_id: Mapped[int]
    episode_arr_id: Mapped[int]
    season: Mapped[int]
    episode: Mapped[int]
    title: Mapped[str]
    tvdb_id: Mapped[int | None]
    monitored: Mapped[bool] = mapped_column(default=False)
    has_file: Mapped[bool] = mapped_column(default=False)
    episode_file_id: Mapped[int | None]
    file_size: Mapped[int | None]
    resolution: Mapped[int | None]
    air_date_utc: Mapped[datetime | None]


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("instance_id", "arr_id", "type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[str]
    arr_id: Mapped[int]
    kind: Mapped[str]
    type: Mapped[str]  # wanted_elsewhere | on_disk
    review_state: Mapped[str] = mapped_column(default="new")  # new|reviewed|ignored|actioned
    lifecycle: Mapped[str] = mapped_column(default="active")  # active | lost | removed
    automation_hold: Mapped[bool] = mapped_column(default=False)
    relevant_count: Mapped[int] = mapped_column(default=0)
    covered_count: Mapped[int] = mapped_column(default=0)
    relevant_episode_ids: Mapped[list[int]] = mapped_column(default=list)
    coverage: Mapped[str]  # full | partial
    best_quality: Mapped[str]
    edition_summary: Mapped[str]
    has_number_matches: Mapped[bool] = mapped_column(default=False)
    first_seen_scan_id: Mapped[int]
    last_seen_scan_id: Mapped[int]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class FindingCopy(Base):
    __tablename__ = "finding_copies"
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    server_id: Mapped[str]
    episode_arr_id: Mapped[int | None]
    resolution: Mapped[int | None]
    hdr: Mapped[bool] = mapped_column(default=False)
    codec: Mapped[str | None]
    edition: Mapped[str | None]
    size: Mapped[int | None]
    quality: Mapped[str]  # better | same | worse | unknown
    edition_cmp: Mapped[str]  # same | different | unknown
    matched_by: Mapped[str]  # id | number
    stale: Mapped[bool] = mapped_column(default=False)


class FindingServerIgnore(Base):
    __tablename__ = "finding_server_ignores"
    __table_args__ = (UniqueConstraint("finding_id", "server_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"))
    server_id: Mapped[str]


class RuleState(Base):
    __tablename__ = "rule_states"
    __table_args__ = (UniqueConstraint("rule_id", "finding_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[str]
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"))
    consecutive_scans: Mapped[int] = mapped_column(default=0)
    coverage_hash: Mapped[str]
    last_counted_scan_id: Mapped[int]


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    status: Mapped[str]  # running | ok | failed
    error: Mapped[str | None]
    trigger: Mapped[str]  # schedule | manual
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ActionLog(Base):
    __tablename__ = "action_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    actor: Mapped[str]  # ui | auto
    rule_id: Mapped[str | None]
    rule_name: Mapped[str | None]
    scan_run_id: Mapped[int | None]
    instance_id: Mapped[str]
    instance_name: Mapped[str]
    finding_id: Mapped[int | None]
    arr_id: Mapped[int]
    item_title: Mapped[str]
    action: Mapped[str]
    scope: Mapped[dict[str, Any]] = mapped_column(default=dict)
    prior_state: Mapped[dict[str, Any]] = mapped_column(default=dict)
    result: Mapped[str]  # ok | error | dry_run
    message: Mapped[str | None]
    undoable: Mapped[bool] = mapped_column(default=False)
    undone_at: Mapped[datetime | None]
    undo_of_id: Mapped[int | None]


class NotificationLog(Base):
    __tablename__ = "notification_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    connection_id: Mapped[str]
    event: Mapped[str]
    success: Mapped[bool]
    error: Mapped[str | None]


class PendingEvent(Base):
    __tablename__ = "pending_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    kind: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    sent: Mapped[bool] = mapped_column(default=False)
