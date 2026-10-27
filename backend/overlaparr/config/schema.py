import re
import uuid
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from overlaparr.cron import validate_cron

_TAG_RE = re.compile(r"[a-z0-9-]+")
_HHMM_RE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_HTTP_URL = TypeAdapter(AnyHttpUrl)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_unique(values: list[str], what: str) -> None:
    dupes = sorted({v for v in values if values.count(v) > 1})
    if dupes:
        raise ValueError(f"duplicate {what}: {dupes}")


class PlexConfig(_Base):
    token: str | None = None
    home_server: str | None = None


class ServerOverride(_Base):
    id: str
    label: str | None = None
    enabled: bool = True


class LossHandling(_Base):
    remonitor: bool = False
    remove_exclusion: bool = False
    quality_profile_id: int | None = None
    search: bool = False


class InstanceConfig(_Base):
    id: str
    name: str
    type: Literal["sonarr", "radarr"]
    url: str
    api_key: str
    enabled: bool = True
    loss_handling: LossHandling = LossHandling()

    @field_validator("url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        _HTTP_URL.validate_python(v)
        return v


class ScanConfig(_Base):
    cron: str = "0 */6 * * *"
    server_timeout_seconds: int = Field(default=10, gt=0)
    server_index_budget_seconds: int = Field(default=600, gt=0)
    page_size: int = Field(default=500, gt=0)
    stale_after_hours: int = Field(default=48, gt=0)

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, v: str) -> str:
        return validate_cron(v)


class TagsConfig(_Base):
    enabled: bool = False
    dupe: str = "dupe-elsewhere"
    partial: str = "dupe-partial"
    ignore: str = "dupe-ignore"
    per_server_tags: bool = False

    @field_validator("dupe", "partial", "ignore")
    @classmethod
    def _tag_name(cls, v: str) -> str:
        if not _TAG_RE.fullmatch(v):
            raise ValueError("tag names must match ^[a-z0-9-]+$")
        return v


class Rule(_Base):
    id: str
    name: str
    enabled: bool = False
    action: Literal["unmonitor", "delete", "exclude", "tag", "ignore"] = "unmonitor"
    delete_mode: Literal["files", "remove_item"] = "files"
    add_exclusion: bool = False
    delete_confirmed: bool = False
    finding_types: list[Literal["wanted_elsewhere", "on_disk"]] = ["wanted_elsewhere"]
    instances: list[str] = []
    servers: list[str] = []
    min_servers: int = Field(default=1, ge=1)
    coverage: Literal["full", "partial"] = "full"
    quality: list[Literal["better", "same", "worse", "unknown"]] = ["better", "same"]
    edition: list[Literal["same", "unknown", "different"]] = ["same"]
    match_methods: list[Literal["id", "number"]] = ["id"]
    wait_scans: int = Field(default=2, ge=1)
    dry_run_next: bool = False

    @model_validator(mode="after")
    def _delete_needs_confirmation(self) -> "Rule":
        if self.action == "delete" and self.enabled and not self.delete_confirmed:
            raise ValueError("enabled delete rules require delete_confirmed=true")
        return self


class NotifyEvents(_Base):
    new_findings: bool = False
    new_findings_types: list[Literal["wanted_elsewhere", "on_disk"]] = []
    new_findings_instances: list[str] = []
    automation_actions: bool = False
    automation_deletes: bool = False
    finding_lost: bool = False
    scan_problems: bool = False
    app_errors: bool = False


class DigestSchedule(_Base):
    mode: Literal["off", "daily", "weekly", "cron"] = "off"
    time: str = "09:00"
    weekday: int = Field(default=0, ge=0, le=6)
    cron: str | None = None

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, v: str | None) -> str | None:
        return v if v is None else validate_cron(v)

    @field_validator("time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM_RE.fullmatch(v):
            raise ValueError("time must be HH:MM (00:00-23:59)")
        return v

    @model_validator(mode="after")
    def _cron_required(self) -> "DigestSchedule":
        if self.mode == "cron" and not self.cron:
            raise ValueError("mode=cron requires cron")
        return self


class _ConnBase(_Base):
    id: str
    name: str
    enabled: bool = False
    events: NotifyEvents = NotifyEvents()
    digest: DigestSchedule = DigestSchedule()


class EmailConn(_ConnBase):
    type: Literal["email"]
    host: str
    port: int = 587
    tls: Literal["starttls", "ssl", "none"] = "starttls"
    username: str | None = None
    password: str | None = None
    from_addr: str
    to: list[str]


class DiscordConn(_ConnBase):
    type: Literal["discord"]
    webhook_url: str


class WebhookConn(_ConnBase):
    type: Literal["webhook"]
    url: str
    method: Literal["POST", "PUT"] = "POST"
    headers: dict[str, str] = {}


class AppriseConn(_ConnBase):
    type: Literal["apprise"]
    urls: list[str]


NotificationConnection = Annotated[
    EmailConn | DiscordConn | WebhookConn | AppriseConn, Field(discriminator="type")
]


class NotificationsConfig(_Base):
    app_url: str | None = None
    connections: list[NotificationConnection] = []

    @field_validator("connections")
    @classmethod
    def _unique_ids(cls, v):
        _check_unique([c.id for c in v], "notification connection ids")
        return v


class LoggingConfig(_Base):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_bytes: int = Field(default=10485760, gt=0)
    backup_count: int = Field(default=5, ge=0)


class ActionLogConfig(_Base):
    retention_days: int | None = None


class AuthConfig(_Base):
    local_bypass: bool = False


class AppConfig(_Base):
    plex: PlexConfig = PlexConfig()
    servers: list[ServerOverride] = []
    instances: list[InstanceConfig] = []
    scan: ScanConfig = ScanConfig()
    tags: TagsConfig = TagsConfig()
    rules: list[Rule] = []
    notifications: NotificationsConfig = NotificationsConfig()
    logging: LoggingConfig = LoggingConfig()
    action_log: ActionLogConfig = ActionLogConfig()
    auth: AuthConfig = AuthConfig()

    @model_validator(mode="after")
    def _uniqueness(self) -> "AppConfig":
        _check_unique([i.id for i in self.instances], "instance ids")
        _check_unique([i.name for i in self.instances], "instance names")
        _check_unique([r.id for r in self.rules], "rule ids")
        _check_unique([s.id for s in self.servers], "server ids")
        return self
