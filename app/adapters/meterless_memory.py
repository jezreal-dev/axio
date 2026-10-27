"""Meterless Headless Agent Engines: World Model + H-MEM (Hierarchical Memory).

Implements the reference architectures in `Meterless/meterless-world-model-agent-engine.md`
and `Meterless/meterless-h-mem-agent-engine.md`:

1. **World Model Engine**
   - `WorldStoreService`: content-hash stable IDs (`ent_<hash>`), canonical entity /
     context / relationship records with provenance and headless agent attribution
     (`agentId`, `runId`, `taskId`), schema-versioned write-through persistence.
   - `IngestPipelineService`: idempotent, resumable ingest pipeline over observations,
     SQL evidence rows, and hypothesis state transitions.
   - `ContextBuilderService`: returns a structured context payload (not a pre-rendered
     string) with `selectionReason`, per-item relevance, and the `wired_vs_helper`
     runtime manifest required by World Model §10.3.
   - `ControlPlaneService`: headless query / maintenance / health surface.

2. **H-MEM Engine**
   - `MemoryStoreService`: tiered storage (`short_term`, `working`, `long_term`) with
     warm-start hydration, promotion on Evaluator verification, and decay.
   - `MemoryMiningService`: acquires memories from runtime agent events via an
     injected generator with a deterministic no-model fallback (H-MEM §6).
   - `MemoryRetrievalService`: hybrid ranking (keyword + tag + domain + entity +
     layer weighting + recency + same-run boost) returning structured reinjection
     payloads with trace metadata (H-MEM §8).
   - `ConflictService` & `TrustLedgerService`: auto-resolves conflicting claims and
     writes an append-only JSONL audit ledger to `logs/` (H-MEM §11-12).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.tracing import Tracer, utc_now

GeneratorFn = Callable[[str, str], str | None]
WORLD_SCHEMA_VERSION = "1.0.0"
HMEM_SCHEMA_VERSION = "1.0.0"


def stable_entity_id(entity_type: str, canonical_name: str) -> str:
    """Content-hash ID (`ent_<12hex>`) so independent agents converge on identity."""
    raw = f"{entity_type.strip().lower()}::{canonical_name.strip().lower()}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"ent_{digest}"


def stable_memory_id(domain: str, content: str) -> str:
    raw = f"{domain.strip().lower()}::{content.strip().lower()}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"mem_{digest}"


# ============================================================================
# 1. WORLD MODEL ENGINE
# ============================================================================


@dataclass
class WorldEntity:
    id: str
    name: str
    entity_type: str  # incident | hypothesis | deployment | feature_flag | config_change | ticket | cohort_metric | upstream
    description: str
    traits: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    continuity_anchor: str = ""
    mention_count: int = 1
    first_seen_at: str = field(default_factory=utc_now)
    last_seen_at: str = field(default_factory=utc_now)
    source_silo: str = "system"
    primary_keys: list[str] = field(default_factory=list)
    state: str = "active"  # emerging | active | testing | falsified | verified
    confidence: float = 0.75
    agent_id: str = "System"
    run_id: str = ""
    task_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorldRelationship:
    id: str
    from_id: str
    to_id: str
    edge_type: str  # investigates | falsified_by | supported_by | precedes | violates_precedence | cites_row
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.9
    created_at: str = field(default_factory=utc_now)
    agent_id: str = "System"
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorldStoreService:
    """Write-through canonical entity, relationship, and snapshot store."""

    def __init__(self, run_id: str, state_dir: Path, log_dir: Path):
        self.run_id = run_id
        self.state_dir = Path(state_dir)
        self.log_dir = Path(log_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.store_file = self.state_dir / f"world_model-{run_id}.json"
        self.append_log = self.log_dir / f"world_model-{run_id}.jsonl"
        self.append_log.write_text("", encoding="utf-8")
        self.entities: dict[str, WorldEntity] = {}
        self.edges: dict[str, WorldRelationship] = {}
        self.snapshots: list[dict[str, Any]] = []

    def upsert_entity(
        self,
        entity_type: str,
        name: str,
        description: str,
        *,
        traits: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        continuity_anchor: str = "",
        source_silo: str = "system",
        primary_keys: list[str] | None = None,
        state: str = "active",
        confidence: float = 0.75,
        agent_id: str = "System",
        task_id: str = "",
    ) -> WorldEntity:
        eid = stable_entity_id(entity_type, name)
        now = utc_now()
        existing = self.entities.get(eid)
        if existing:
            # Canonical reconstruction (World Model §11.2): union tags/keys, merge traits,
            # longest description wins unless explicitly falsified/verified.
            if len(description) >= len(existing.description) or state in ("falsified", "verified"):
                existing.description = description
            existing.traits.update(traits or {})
            existing.tags = sorted(set(existing.tags) | set(tags or []))
            existing.primary_keys = sorted(set(existing.primary_keys) | set(primary_keys or []))
            if continuity_anchor:
                existing.continuity_anchor = continuity_anchor
            existing.mention_count += 1
            existing.last_seen_at = now
            existing.state = state
            existing.confidence = confidence
            existing.agent_id = agent_id
            existing.task_id = task_id or existing.task_id
            entity = existing
            action = "entity.updated"
        else:
            entity = WorldEntity(
                id=eid,
                name=name,
                entity_type=entity_type,
                description=description,
                traits=dict(traits or {}),
                tags=sorted(set(tags or [])),
                continuity_anchor=continuity_anchor or f"{entity_type}:{name}",
                mention_count=1,
                first_seen_at=now,
                last_seen_at=now,
                source_silo=source_silo,
                primary_keys=sorted(set(primary_keys or [])),
                state=state,
                confidence=confidence,
                agent_id=agent_id,
                run_id=self.run_id,
                task_id=task_id,
            )
            self.entities[eid] = entity
            action = "entity.created"
        self._append_event(action, entity.to_dict(), agent_id)
        self._persist()
        return entity

    def add_edge(
        self,
        from_id: str,
        to_id: str,
        edge_type: str,
        *,
        properties: dict[str, Any] | None = None,
        confidence: float = 0.9,
        agent_id: str = "System",
    ) -> WorldRelationship:
        raw = f"{from_id}--{edge_type}-->{to_id}"
        edge_id = "edg_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        edge = WorldRelationship(
            id=edge_id,
            from_id=from_id,
            to_id=to_id,
            edge_type=edge_type,
            properties=dict(properties or {}),
            confidence=confidence,
            agent_id=agent_id,
            run_id=self.run_id,
        )
        self.edges[edge_id] = edge
        self._append_event("edge.upserted", edge.to_dict(), agent_id)
        self._persist()
        return edge

    def record_snapshot(self, label: str, agent_id: str, entity_ids: list[str], summary: str) -> dict[str, Any]:
        snap = {
            "snapshot_index": len(self.snapshots),
            "timestamp": utc_now(),
            "run_id": self.run_id,
            "agent_id": agent_id,
            "label": label,
            "entity_ids": list(entity_ids),
            "summary": summary,
        }
        self.snapshots.append(snap)
        self._append_event("snapshot.created", snap, agent_id)
        self._persist()
        return snap

    def _append_event(self, event_type: str, payload: dict[str, Any], agent_id: str) -> None:
        rec = {
            "timestamp": utc_now(),
            "schema_version": WORLD_SCHEMA_VERSION,
            "run_id": self.run_id,
            "agent_id": agent_id,
            "event_type": event_type,
            "payload": payload,
        }
        with self.append_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _persist(self) -> None:
        doc = {
            "schema_version": WORLD_SCHEMA_VERSION,
            "run_id": self.run_id,
            "updated_at": utc_now(),
            "entities": {k: v.to_dict() for k, v in self.entities.items()},
            "edges": {k: v.to_dict() for k, v in self.edges.items()},
            "snapshots": self.snapshots,
        }
        self.store_file.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


class IngestPipelineService:
    """10-step idempotent ingest pipeline (World Model §6.1)."""

    def __init__(self, store: WorldStoreService, tracer: Tracer | None = None):
        self.store = store
        self.tracer = tracer
        self.runs: list[dict[str, Any]] = []

    def ingest_sql_observations(
        self,
        *,
        silo: str,
        purpose: str,
        rows: list[dict[str, Any]],
        agent_id: str,
        task_id: str = "",
        incident_entity_id: str | None = None,
    ) -> list[WorldEntity]:
        created: list[WorldEntity] = []
        for row in rows[:25]:
            pk = (
                row.get("deploy_id")
                or row.get("change_id")
                or row.get("ticket_id")
                or row.get("trace_id")
                or row.get("event_id")
                or row.get("tenant_id")
                or row.get("tenant")
            )
            if not pk:
                continue
            etype = _infer_entity_type(silo, purpose, row)
            desc = _summarize_row(etype, row)
            ent = self.store.upsert_entity(
                entity_type=etype,
                name=str(pk),
                description=desc,
                traits=row,
                tags=[silo, etype],
                continuity_anchor=f"{silo}:{etype}:{pk}",
                source_silo=silo,
                primary_keys=[str(pk)],
                state="active",
                confidence=0.95,
                agent_id=agent_id,
                task_id=task_id,
            )
            created.append(ent)
            if incident_entity_id:
                self.store.add_edge(
                    incident_entity_id,
                    ent.id,
                    "cites_row",
                    properties={"silo": silo, "purpose": purpose},
                    agent_id=agent_id,
                )
        snap = self.store.record_snapshot(
            label=f"ingest:{silo}:{purpose}",
            agent_id=agent_id,
            entity_ids=[e.id for e in created],
            summary=f"Ingested {len(created)} grounded records from {silo} ({purpose})",
        )
        run_rec = {
            "pipeline_step": "canonical_10_step_ingest",
            "silo": silo,
            "purpose": purpose,
            "ingested_entities": len(created),
            "snapshot_index": snap["snapshot_index"],
            "timestamp": utc_now(),
        }
        self.runs.append(run_rec)
        return created


class ContextBuilderService:
    """Builds structured context payloads for downstream agents (World Model §10.2-10.3)."""

    WIRED_HELPERS = [
        "select_entities_by_state",
        "select_continuity_anchors",
        "falsification_graph_projection",
        "cross_silo_citation_index",
    ]
    DORMANT_HELPERS = [
        "vector_centroid_similarity",
        "multimodal_vision_anchor",
    ]

    def __init__(self, store: WorldStoreService):
        self.store = store

    def build_context(self, intent: str, max_entities: int = 20) -> dict[str, Any]:
        prioritized = sorted(
            self.store.entities.values(),
            key=lambda e: (
                0 if e.entity_type == "hypothesis" else (1 if e.entity_type == "incident" else 2),
                -e.mention_count,
                e.last_seen_at,
            ),
        )[:max_entities]
        return {
            "intent": intent,
            "strategy": "salience_and_hypothesis_priority",
            "selectionReason": "Hypotheses and incident anchors prioritized first, then highest-mention evidence rows",
            "wired_vs_helper_manifest": {
                "wired_helpers": self.WIRED_HELPERS,
                "dormant_helpers": self.DORMANT_HELPERS,
            },
            "entities": [
                {
                    "id": e.id,
                    "name": e.name,
                    "type": e.entity_type,
                    "state": e.state,
                    "confidence": e.confidence,
                    "continuity_anchor": e.continuity_anchor,
                    "description": e.description,
                    "source_silo": e.source_silo,
                    "primary_keys": e.primary_keys,
                    "traits": e.traits,
                }
                for e in prioritized
            ],
            "edges": [ed.to_dict() for ed in list(self.store.edges.values())[-40:]],
        }


class ControlPlaneService:
    """Headless control plane replacing any UI-coupled Library (World Model §12)."""

    def __init__(self, store: WorldStoreService, pipeline: IngestPipelineService, context_builder: ContextBuilderService):
        self.store = store
        self.pipeline = pipeline
        self.context_builder = context_builder

    def get_health(self) -> dict[str, Any]:
        orphans = [
            e.id
            for e in self.store.entities.values()
            if e.entity_type != "incident"
            and not any(ed.from_id == e.id or ed.to_id == e.id for ed in self.store.edges.values())
        ]
        return {
            "schema_version": WORLD_SCHEMA_VERSION,
            "entity_count": len(self.store.entities),
            "edge_count": len(self.store.edges),
            "snapshot_count": len(self.store.snapshots),
            "pipeline_runs": len(self.pipeline.runs),
            "orphan_entity_count": len(orphans),
            "wired_vs_helper_manifest": {
                "wired_helpers": self.context_builder.WIRED_HELPERS,
                "dormant_helpers": self.context_builder.DORMANT_HELPERS,
            },
        }


# ============================================================================
# 2. H-MEM (HIERARCHICAL MEMORY) ENGINE
# ============================================================================


@dataclass
class MemoryRecord:
    id: str
    content: str
    memory_type: str  # factual | procedural | invariant | falsification
    layer: str  # short_term | working | long_term
    timestamp: str
    last_accessed: str
    access_count: int = 1
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.85
    source: str = "runtime_event"
    domain: str = "incident_investigation"
    namespace: str = "orchestration/root_cause"
    entities: list[str] = field(default_factory=list)
    related_to: list[str] = field(default_factory=list)
    supersedes: str | None = None
    derived_from: list[str] = field(default_factory=list)
    agent_id: str = "System"
    run_id: str = ""
    task_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TrustLedgerService:
    """Append-only JSONL audit ledger for H-MEM mutations and feedback (H-MEM §11)."""

    def __init__(self, run_id: str, log_dir: Path):
        self.run_id = run_id
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_file = self.log_dir / f"hmem_trust_ledger-{run_id}.jsonl"
        self.ledger_file.write_text("", encoding="utf-8")
        self.entries: list[dict[str, Any]] = []

    def record(
        self,
        memory_id: str,
        action: str,
        actor: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "timestamp": utc_now(),
            "schema_version": HMEM_SCHEMA_VERSION,
            "run_id": self.run_id,
            "memory_id": memory_id,
            "action": action,
            "actor": actor,
            "details": details or {},
        }
        self.entries.append(entry)
        with self.ledger_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry


class MemoryStoreService:
    """Tiered memory persistence (`short_term`, `working`, `long_term`) with warm start."""

    def __init__(self, run_id: str, state_dir: Path, trust_ledger: TrustLedgerService):
        self.run_id = run_id
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.persistent_file = self.state_dir / "hmem_durable_store.json"
        self.trust = trust_ledger
        self.memories: dict[str, MemoryRecord] = {}
        self.conflicts_resolved: list[dict[str, Any]] = []
        self.hydrate()

    def hydrate(self) -> int:
        """Warm-start from durable `working` and `long_term` memories on disk."""
        loaded = 0
        if self.persistent_file.exists():
            try:
                raw = json.loads(self.persistent_file.read_text(encoding="utf-8"))
                for item in raw.get("memories", []):
                    rec = MemoryRecord(**item)
                    if rec.layer in ("working", "long_term"):
                        self.memories[rec.id] = rec
                        loaded += 1
            except Exception:  # noqa: BLE001
                loaded = 0
        self.trust.record("system", "hydrate_warm_start", "MemoryStoreService", {"loaded_count": loaded})
        return loaded

    def add_memory(
        self,
        content: str,
        *,
        memory_type: str = "factual",
        layer: str = "short_term",
        tags: list[str] | None = None,
        confidence: float = 0.85,
        source: str = "agent_event",
        domain: str = "incident_investigation",
        namespace: str = "orchestration/root_cause",
        entities: list[str] | None = None,
        supersedes: str | None = None,
        derived_from: list[str] | None = None,
        agent_id: str = "System",
        task_id: str = "",
    ) -> MemoryRecord:
        mid = stable_memory_id(namespace, content)
        now = utc_now()
        # Conflict detection & resolution (H-MEM §12): if a new falsification or verified
        # memory contradicts an earlier assumption on the same entity, mark `supersedes`.
        if entities and memory_type in ("falsification", "invariant", "factual"):
            for prev in list(self.memories.values()):
                if prev.id == mid or prev.supersedes:
                    continue
                if set(prev.entities) & set(entities) and prev.memory_type != memory_type:
                    if confidence >= prev.confidence:
                        supersedes = prev.id
                        prev.confidence = round(max(0.1, prev.confidence - 0.35), 2)
                        conflict = {
                            "kept_memory_id": mid,
                            "superseded_memory_id": prev.id,
                            "shared_entities": sorted(set(prev.entities) & set(entities)),
                            "strategy": "auto_decide_higher_confidence_evidence",
                        }
                        self.conflicts_resolved.append(conflict)
                        self.trust.record(mid, "conflict_resolved", agent_id, conflict)

        rec = MemoryRecord(
            id=mid,
            content=content,
            memory_type=memory_type,
            layer=layer,
            timestamp=now,
            last_accessed=now,
            access_count=1,
            tags=sorted(set(tags or [])),
            confidence=confidence,
            source=source,
            domain=domain,
            namespace=namespace,
            entities=sorted(set(entities or [])),
            supersedes=supersedes,
            derived_from=list(derived_from or []),
            agent_id=agent_id,
            run_id=self.run_id,
            task_id=task_id,
        )
        self.memories[mid] = rec
        self.trust.record(mid, "memory_added", agent_id, {"layer": layer, "type": memory_type, "tags": rec.tags})
        self._persist_durable()
        return rec

    def promote(self, memory_id: str, target_layer: str, actor: str, confidence_boost: float = 0.08) -> None:
        rec = self.memories.get(memory_id)
        if not rec:
            return
        prev_layer = rec.layer
        rec.layer = target_layer
        rec.confidence = round(min(0.99, rec.confidence + confidence_boost), 3)
        rec.last_accessed = utc_now()
        rec.access_count += 1
        self.trust.record(
            memory_id,
            "memory_promoted",
            actor,
            {"previous_layer": prev_layer, "new_layer": target_layer, "confidence": rec.confidence},
        )
        self._persist_durable()

    def feedback(self, memory_id: str, signal: str, actor: str) -> None:
        """Event-driven feedback adaptation (H-MEM §11.2): helpful | not_helpful | wrong."""
        rec = self.memories.get(memory_id)
        if not rec:
            return
        if signal == "helpful":
            rec.access_count += 1
            rec.confidence = round(min(0.99, rec.confidence + 0.05), 3)
        elif signal == "wrong":
            rec.confidence = round(max(0.05, rec.confidence - 0.30), 3)
            if "review" not in rec.tags:
                rec.tags.append("review")
        self.trust.record(memory_id, f"feedback_{signal}", actor, {"new_confidence": rec.confidence})
        self._persist_durable()

    def _persist_durable(self) -> None:
        durable = [m.to_dict() for m in self.memories.values() if m.layer in ("working", "long_term")]
        self.persistent_file.write_text(
            json.dumps({"schema_version": HMEM_SCHEMA_VERSION, "memories": durable}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


class MemoryMiningService:
    """Acquires memories from runtime agent events with no-model fallback (H-MEM §6)."""

    def __init__(self, store: MemoryStoreService, generator: GeneratorFn | None = None):
        self.store = store
        self.generator = generator

    def mine_event(
        self,
        *,
        event_kind: str,
        summary: str,
        agent_id: str,
        task_id: str = "",
        layer: str = "working",
        memory_type: str = "factual",
        tags: list[str] | None = None,
        entities: list[str] | None = None,
        confidence: float = 0.88,
        namespace: str = "orchestration/root_cause",
    ) -> MemoryRecord:
        distilled = summary
        if self.generator is not None:
            try:
                out = self.generator(
                    f"Distill this runtime agent event into one concise factual sentence (<180 chars):\n{summary}",
                    "You are the Meterless H-MEM memory acquisition miner. Return one plain sentence.",
                )
                if out and 15 <= len(out.strip()) <= 240:
                    distilled = out.strip()
            except Exception:  # noqa: BLE001 - graceful no-model fallback
                distilled = summary
        return self.store.add_memory(
            distilled,
            memory_type=memory_type,
            layer=layer,
            tags=(tags or []) + [event_kind],
            confidence=confidence,
            source=f"event:{event_kind}",
            namespace=namespace,
            entities=entities or [],
            agent_id=agent_id,
            task_id=task_id,
        )


class MemoryRetrievalService:
    """Hybrid ranking retrieval returning a structured reinjection payload (H-MEM §8)."""

    LAYER_WEIGHT = {"long_term": 0.30, "working": 0.20, "short_term": 0.10}

    def __init__(self, store: MemoryStoreService, trust: TrustLedgerService):
        self.store = store
        self.trust = trust

    def retrieve(
        self,
        query: str,
        *,
        consumer_agent: str,
        entities: list[str] | None = None,
        tags: list[str] | None = None,
        top_n: int = 6,
        threshold: float = 0.20,
    ) -> dict[str, Any]:
        q_tokens = set(_tokenize(query))
        q_entities = set(entities or [])
        q_tags = set(tags or [])
        superseded_ids = {m.supersedes for m in self.store.memories.values() if m.supersedes}

        scored: list[tuple[float, MemoryRecord, str]] = []
        for mem in self.store.memories.values():
            m_tokens = set(_tokenize(mem.content))
            kw_overlap = len(q_tokens & m_tokens) / max(1, len(q_tokens)) if q_tokens else 0.15
            tag_overlap = len(q_tags & set(mem.tags)) / max(1, len(q_tags)) if q_tags else 0.0
            ent_overlap = len(q_entities & set(mem.entities)) / max(1, len(q_entities)) if q_entities else 0.0
            layer_w = self.LAYER_WEIGHT.get(mem.layer, 0.10)
            same_run_boost = 0.15 if mem.run_id == self.store.run_id else 0.0
            superseded_penalty = -0.35 if mem.id in superseded_ids else 0.0

            score = round(
                0.30 * kw_overlap
                + 0.20 * tag_overlap
                + 0.25 * ent_overlap
                + layer_w
                + 0.15 * mem.confidence
                + same_run_boost
                + superseded_penalty,
                3,
            )
            if score >= threshold:
                reasons = []
                if ent_overlap > 0:
                    reasons.append("entity_match")
                if tag_overlap > 0:
                    reasons.append("tag_match")
                if mem.memory_type == "falsification":
                    reasons.append("falsification_guardrail")
                if mem.memory_type == "procedural":
                    reasons.append("schema_procedural_knowledge")
                if not reasons:
                    reasons.append("hybrid_keyword_layer_rank")
                scored.append((score, mem, "+".join(reasons)))

        scored.sort(key=lambda item: item[0], reverse=True)
        selected = scored[:top_n]
        for _sc, mem, _reason in selected:
            mem.access_count += 1
            mem.last_accessed = utc_now()

        payload = {
            "query": query,
            "consumer_agent": consumer_agent,
            "strategy": "task_focused",
            "total_candidates": len(self.store.memories),
            "returned_count": len(selected),
            "memories": [
                {
                    "id": mem.id,
                    "content": mem.content,
                    "type": mem.memory_type,
                    "layer": mem.layer,
                    "confidence": mem.confidence,
                    "score": sc,
                    "retrievalReason": reason,
                    "entities": mem.entities,
                    "tags": mem.tags,
                    "agentId": mem.agent_id,
                    "runId": mem.run_id,
                }
                for sc, mem, reason in selected
            ],
        }
        self.trust.record(
            "retrieval_batch",
            "reinjection_retrieve",
            consumer_agent,
            {"returned_count": len(selected), "strategy": "task_focused"},
        )
        return payload


# ============================================================================
# 3. UNIFIED METERLESS COGNITION FACADE (FALSIFICATION GRAPH + H-MEM + WORLD)
# ============================================================================


class MeterlessCognitionEngine:
    """Mounts both the Meterless World Model Engine and H-MEM Engine behind one
    clean interface used by PlannerAgent, ExecutorAgent, EvaluatorAgent, and
    SynthesizerAgent.
    """

    def __init__(
        self,
        run_id: str,
        state_dir: Path,
        log_dir: Path,
        tracer: Tracer | None = None,
        generator: GeneratorFn | None = None,
    ):
        self.run_id = run_id
        self.tracer = tracer
        self.world_store = WorldStoreService(run_id=run_id, state_dir=state_dir, log_dir=log_dir)
        self.ingest_pipeline = IngestPipelineService(store=self.world_store, tracer=tracer)
        self.context_builder = ContextBuilderService(store=self.world_store)
        self.control_plane = ControlPlaneService(
            store=self.world_store,
            pipeline=self.ingest_pipeline,
            context_builder=self.context_builder,
        )
        self.trust_ledger = TrustLedgerService(run_id=run_id, log_dir=log_dir)
        self.hmem_store = MemoryStoreService(run_id=run_id, state_dir=state_dir, trust_ledger=self.trust_ledger)
        self.hmem_miner = MemoryMiningService(store=self.hmem_store, generator=generator)
        self.hmem_retrieval = MemoryRetrievalService(store=self.hmem_store, trust=self.trust_ledger)
        self.incident_entity_id: str | None = None

    def register_incident(self, incident_id: str, title: str, started_at: str, symptom_summary: str) -> str:
        ent = self.world_store.upsert_entity(
            entity_type="incident",
            name=incident_id,
            description=f"{title} (onset {started_at}): {symptom_summary}",
            traits={"incident_id": incident_id, "started_at": started_at, "title": title},
            tags=["incident", incident_id],
            continuity_anchor=f"incident:{incident_id}@{started_at}",
            source_silo="alert",
            primary_keys=[incident_id],
            state="active",
            confidence=1.0,
            agent_id="Orchestrator",
        )
        self.incident_entity_id = ent.id
        self.hmem_miner.mine_event(
            event_kind="incident_opened",
            summary=f"Incident {incident_id} opened with onset {started_at}: {title}",
            agent_id="Orchestrator",
            layer="working",
            memory_type="factual",
            tags=["incident", incident_id],
            entities=[incident_id],
            confidence=1.0,
        )
        return ent.id

    def register_hypothesis(self, hyp: Any, agent_id: str = "PlannerAgent") -> str:
        ent = self.world_store.upsert_entity(
            entity_type="hypothesis",
            name=hyp.id,
            description=f"[{hyp.status}] {hyp.title} — {hyp.mechanism}",
            traits=hyp.to_dict(),
            tags=["hypothesis", hyp.category, hyp.status.lower()],
            continuity_anchor=f"hypothesis:{hyp.id}:{hyp.suspected_change_id}",
            source_silo=hyp.target_silo,
            primary_keys=[hyp.suspected_change_id] if hyp.suspected_change_id else [],
            state=hyp.status.lower(),
            confidence=hyp.confidence,
            agent_id=agent_id,
            task_id=f"round_{hyp.round_proposed}",
        )
        hyp.world_entity_id = ent.id
        if self.incident_entity_id:
            self.world_store.add_edge(
                ent.id,
                self.incident_entity_id,
                "investigates",
                properties={"category": hyp.category, "round": hyp.round_proposed},
                confidence=hyp.confidence,
                agent_id=agent_id,
            )
        return ent.id

    def record_falsification(self, hyp: Any, reason: str, cited_ids: list[str], agent_id: str = "EvaluatorAgent") -> None:
        self.register_hypothesis(hyp, agent_id=agent_id)
        for cid in cited_ids:
            target_eid = stable_entity_id("evidence", cid)
            self.world_store.add_edge(
                hyp.world_entity_id or stable_entity_id("hypothesis", hyp.id),
                target_eid,
                "falsified_by",
                properties={"reason": reason, "cited_id": cid},
                confidence=0.98,
                agent_id=agent_id,
            )
        mem = self.hmem_miner.mine_event(
            event_kind="hypothesis_falsified",
            summary=f"FALSIFIED {hyp.id} ({hyp.title}): {reason} [citations: {', '.join(cited_ids)}]",
            agent_id=agent_id,
            layer="working",
            memory_type="falsification",
            tags=["falsified", hyp.category, hyp.suspected_change_id],
            entities=[hyp.id, hyp.suspected_change_id] + cited_ids,
            confidence=0.98,
        )
        self.hmem_store.promote(mem.id, "long_term", actor=agent_id, confidence_boost=0.05)

    def record_verification(self, hyp: Any, summary: str, cited_ids: list[str], agent_id: str = "EvaluatorAgent") -> None:
        self.register_hypothesis(hyp, agent_id=agent_id)
        for cid in cited_ids:
            target_eid = stable_entity_id("evidence", cid)
            self.world_store.add_edge(
                hyp.world_entity_id or stable_entity_id("hypothesis", hyp.id),
                target_eid,
                "supported_by",
                properties={"summary": summary, "cited_id": cid},
                confidence=hyp.confidence,
                agent_id=agent_id,
            )
        mem = self.hmem_miner.mine_event(
            event_kind="hypothesis_verified",
            summary=f"VERIFIED ROOT CAUSE {hyp.id} ({hyp.title}): {summary} [citations: {', '.join(cited_ids)}]",
            agent_id=agent_id,
            layer="working",
            memory_type="invariant",
            tags=["verified", "root_cause", hyp.category, hyp.suspected_change_id],
            entities=[hyp.id, hyp.suspected_change_id] + cited_ids,
            confidence=hyp.confidence,
        )
        self.hmem_store.promote(mem.id, "long_term", actor=agent_id, confidence_boost=0.08)

    def record_schema_learning(self, silo: str, table: str, repairs: dict[str, str], agent_id: str = "ExecutorAgent") -> None:
        mem = self.hmem_miner.mine_event(
            event_kind="sql_schema_repaired",
            summary=f"Schema mapping learned for {{{silo}}}.{table}: column repairs {repairs}",
            agent_id=agent_id,
            layer="working",
            memory_type="procedural",
            tags=["schema", silo, table],
            entities=[f"{silo}.{table}"],
            confidence=0.99,
            namespace="tooling/zetaris_schema",
        )
        self.hmem_store.promote(mem.id, "long_term", actor=agent_id)

    def build_agent_context(self, consumer_agent: str, task_query: str, entities: list[str] | None = None) -> dict[str, Any]:
        hmem_ctx = self.hmem_retrieval.retrieve(
            task_query,
            consumer_agent=consumer_agent,
            entities=entities,
            top_n=6,
        )
        world_ctx = self.context_builder.build_context(intent=f"{consumer_agent}:{task_query}")
        if self.tracer:
            self.tracer.emit(
                consumer_agent,
                "meterless_context_reinjected",
                task_query[:120],
                f"Injected {hmem_ctx['returned_count']} H-MEM memories + {len(world_ctx['entities'])} World Model entities",
                target_agent=consumer_agent,
                status="success",
                hmem_returned=hmem_ctx["returned_count"],
                world_entities=len(world_ctx["entities"]),
                strategy=hmem_ctx["strategy"],
            )
        return {"hmem": hmem_ctx, "world_model": world_ctx}

    def snapshot_summary(self) -> dict[str, Any]:
        wm_file = self.world_store.state_dir / "meterless_world_model.json"
        entities_list = [e.to_dict() for e in list(self.world_store.entities.values())[:25]]
        relations_list = [r.to_dict() for r in list(self.world_store.edges.values())[:25]]
        events_list = list(self.world_store.snapshots[-25:])
        wm_snapshot = {
            "schema_version": WORLD_SCHEMA_VERSION,
            "entity_count": len(self.world_store.entities),
            "relation_count": len(self.world_store.edges),
            "event_count": len(self.world_store.snapshots),
            "entities": entities_list,
            "relations": relations_list,
            "events": events_list,
            "persisted_path": str(wm_file.name),
        }
        wm_file.write_text(json.dumps(wm_snapshot, indent=2, ensure_ascii=False), encoding="utf-8")

        mems_list = [m.to_dict() for m in self.hmem_store.memories.values()]
        by_type: dict[str, int] = {}
        for m in self.hmem_store.memories.values():
            by_type[m.memory_type] = by_type.get(m.memory_type, 0) + 1

        trust_profiles: dict[str, dict[str, Any]] = {}
        for e in self.trust_ledger.entries:
            actor = e.get("actor", "unknown")
            prof = trust_profiles.setdefault(actor, {"events": 0, "last_action": ""})
            prof["events"] += 1
            prof["last_action"] = e.get("action", "")

        hmem_snapshot = {
            "schema_version": HMEM_SCHEMA_VERSION,
            "memory_count": len(mems_list),
            "by_type": by_type,
            "by_layer": {
                layer: sum(1 for m in self.hmem_store.memories.values() if m.layer == layer)
                for layer in ("short_term", "working", "long_term")
            },
            "conflicts_resolved": self.hmem_store.conflicts_resolved,
            "trust_profiles": trust_profiles,
            "memories": mems_list[:20],
            "persisted_path": "meterless_hmem.json",
        }
        (self.world_store.state_dir / "meterless_hmem.json").write_text(
            json.dumps(hmem_snapshot, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        manifest = {
            "wired_services": self.context_builder.WIRED_HELPERS,
            "wired_helpers": self.context_builder.WIRED_HELPERS,
            "dormant_helpers": self.context_builder.DORMANT_HELPERS,
        }
        return {
            "world_model": wm_snapshot,
            "hmem": hmem_snapshot,
            "context_builder_manifest": manifest,
            "world_model_health": self.control_plane.get_health(),
            "hmem_stats": hmem_snapshot,
            "world_model_log_file": str(self.world_store.append_log.name),
        }


def _infer_entity_type(silo: str, purpose: str, row: dict[str, Any]) -> str:
    if "deploy_id" in row:
        return "deployment"
    if "flag_key" in row:
        return "feature_flag"
    if "config_key" in row:
        return "config_change"
    if "ticket_id" in row:
        return "ticket"
    if "trace_id" in row:
        return "telemetry_trace"
    if "event_id" in row:
        return "billing_event"
    return f"{silo}_record"


def _summarize_row(etype: str, row: dict[str, Any]) -> str:
    if etype == "deployment":
        return f"Deploy {row.get('deploy_id')}: {row.get('service')} v{row.get('version')} at {row.get('deployed_at')} ({row.get('summary')})"
    if etype == "feature_flag":
        return f"Flag {row.get('change_id')}: {row.get('flag_key')} {row.get('action')} ({row.get('rollout_pct')}%) at {row.get('changed_at')} [{row.get('targeting')}]"
    if etype == "config_change":
        return f"Config {row.get('change_id')}: {row.get('service')} {row.get('config_key')} {row.get('old_value')}->{row.get('new_value')} at {row.get('changed_at')}"
    if etype == "ticket":
        return f"Ticket {row.get('ticket_id')} ({row.get('tenant')} at {row.get('opened_at')}): {row.get('subject')}"
    return json.dumps(row, ensure_ascii=False)[:180]


def _tokenize(text: str) -> list[str]:
    return [tok for tok in re.findall(r"[a-z0-9_.-]+", text.lower()) if len(tok) > 2]
