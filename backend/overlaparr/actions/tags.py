"""Tag mirror: keep tool-owned tags in Sonarr/Radarr in step with the findings in the DB."""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from sqlalchemy import select

from overlaparr.arr.client import ArrClient, ArrError
from overlaparr.db.models import Finding, FindingCopy, FindingServerIgnore, Server, TrackedItem
from overlaparr.logging_setup import redact
from overlaparr.scan.engine import ScanContext

log = logging.getLogger(__name__)

_PREFIX = "on-"
_BAD = re.compile(r"[^a-z0-9-]+")


def _normalize(label: str) -> str:
    return re.sub(r"-{2,}", "-", _BAD.sub("-", label.lower())).strip("-")


def _server_labels(ctx: ScanContext) -> dict[str, str]:
    """Server id -> normalized label (override label, else server name); empty ones dropped."""
    override = {o.id: o.label for o in ctx.config.servers if o.label}
    labels = {s.machine_id: override.get(s.machine_id) or s.name
              for s in ctx.db.scalars(select(Server))}
    labels.update(override)
    normalized = {sid: _normalize(lbl) for sid, lbl in labels.items()}
    return {sid: lbl for sid, lbl in normalized.items() if lbl}


def _supported(ctx: ScanContext, instance_id: str) -> dict[int, tuple[bool, set[str]]]:
    """arr_id -> (any supporting finding partial, server ids with a counting copy)."""
    findings = ctx.db.scalars(
        select(Finding).where(
            Finding.instance_id == instance_id,
            Finding.lifecycle == "active",
            Finding.review_state != "ignored",
        )
    ).all()
    out: dict[int, tuple[bool, set[str]]] = {}
    for f in findings:
        ignored = set(ctx.db.scalars(
            select(FindingServerIgnore.server_id).where(FindingServerIgnore.finding_id == f.id)
        ))
        # Stale copies still count: an offline server must never cost an item its tag.
        servers = set(ctx.db.scalars(
            select(FindingCopy.server_id).where(FindingCopy.finding_id == f.id)
        )) - ignored
        if not servers:
            continue
        partial, have = out.get(f.arr_id, (False, set()))
        out[f.arr_id] = (partial or f.coverage == "partial", have | servers)
    return out


def _sync_instance(ctx: ScanContext, instance_id: str, client: ArrClient,
                   server_labels: dict[str, str]) -> None:
    cfg = ctx.config.tags
    tag_label = {t["id"]: str(t["label"]).lower() for t in client.get("/api/v3/tag")}
    label_tag = {lbl: tid for tid, lbl in tag_label.items()}
    owned = {cfg.dupe, cfg.partial} | {_PREFIX + lbl for lbl in server_labels.values()}
    owned.discard(cfg.ignore)  # the user's ignore tag is never the tool's to remove

    support = _supported(ctx, instance_id)
    items = ctx.db.scalars(
        select(TrackedItem).where(TrackedItem.instance_id == instance_id)
    ).all()

    to_add: dict[str, list[TrackedItem]] = defaultdict(list)
    to_remove: dict[int, list[TrackedItem]] = defaultdict(list)
    for item in items:
        partial, servers = support.get(item.arr_id, (False, set()))
        desired: set[str] = set()
        if servers:
            desired.add(cfg.dupe)
            if partial:
                desired.add(cfg.partial)
            if cfg.per_server_tags:
                desired |= {_PREFIX + server_labels[s] for s in servers if s in server_labels}
        desired.discard(cfg.ignore)
        current = {tag_label[t] for t in item.tag_ids or [] if t in tag_label}
        for lbl in sorted(desired - current):
            to_add[lbl].append(item)
        for lbl in sorted((current & owned) - desired):
            to_remove[label_tag[lbl]].append(item)

    def apply(tag_id: int, group: list[TrackedItem], mode: str) -> None:
        client.edit_tags([i.arr_id for i in group], [tag_id], mode)  # type: ignore[attr-defined]
        for i in group:
            ids = [t for t in (i.tag_ids or []) if t != tag_id]
            i.tag_ids = [*ids, tag_id] if mode == "add" else ids
        ctx.db.commit()

    for tag_id, group in to_remove.items():
        apply(tag_id, group, "remove")
    for lbl, group in to_add.items():
        apply(label_tag.get(lbl) or client.ensure_tag(lbl), group, "add")


def sync_tags(ctx: ScanContext) -> None:
    """Scan post-hook: mirror dupe/partial/per-server tags onto items. Not an action."""
    if not ctx.config.tags.enabled or ctx.dry_run:
        return
    server_labels = _server_labels(ctx)
    for instance_id, client in ctx.clients.items():
        if ctx.instance_ok.get(instance_id) is not True:
            continue
        try:
            _sync_instance(ctx, instance_id, client, server_labels)
        except ArrError as e:
            ctx.db.rollback()
            log.warning("tag sync failed for instance %s: %s", instance_id, redact(e.message))
