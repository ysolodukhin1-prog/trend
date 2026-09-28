"""PostgreSQL decision ledger. Not connected to a production sender or HTTP mutation route."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json

from psycopg2.extras import Json, RealDictCursor

from .domain import MetricSnapshot, Policy


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class Repository:
    def __init__(self, conn_factory, client: str):
        self.conn_factory, self.client = conn_factory, client

    def save_snapshot(self, snapshot: MetricSnapshot) -> int:
        obj = snapshot.object
        if obj.client != self.client: raise PermissionError("Чужой клиент")
        identity = (obj.client, obj.marketplace, obj.account, obj.campaign, obj.sku, obj.placement, obj.unit)
        metrics = {"current_bid": snapshot.current_bid, "observations": snapshot.observations,
                   "spend": str(snapshot.spend) if snapshot.spend is not None else None,
                   "attributed_revenue": str(snapshot.attributed_revenue) if snapshot.attributed_revenue is not None else None,
                   "stock": snapshot.stock, "changes_today": snapshot.changes_today,
                   "last_change_at": snapshot.last_change_at.isoformat() if snapshot.last_change_at else None}
        fingerprint = _fingerprint({"identity": identity, "captured_at": snapshot.captured_at.isoformat(),
                                    "source": snapshot.source, "metrics": metrics, "coverage": snapshot.coverage,
                                    "maturity": snapshot.maturity})
        with closing(self.conn_factory()) as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""INSERT INTO public.autobid_objects VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING""", identity)
            cur.execute("""INSERT INTO public.autobid_snapshots
                 (client,marketplace,account,campaign,sku,placement,unit,captured_at,source,coverage,maturity,metrics,fingerprint)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 ON CONFLICT (fingerprint) DO NOTHING RETURNING id""",
                        (*identity, snapshot.captured_at, snapshot.source, snapshot.coverage,
                         snapshot.maturity, Json(metrics), fingerprint))
            row = cur.fetchone()
            if row: return row["id"]
            cur.execute("SELECT id FROM public.autobid_snapshots WHERE fingerprint=%s AND client=%s", (fingerprint, self.client))
            return cur.fetchone()["id"]

    def save_policy(self, snapshot_id: int, policy: Policy) -> str:
        spec = {**asdict(policy), "target_drr": str(policy.target_drr)}
        with closing(self.conn_factory()) as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT client,marketplace,account,campaign,sku,placement,unit
                FROM public.autobid_snapshots WHERE id=%s AND client=%s""", (snapshot_id, self.client))
            obj = cur.fetchone()
            if not obj: raise PermissionError("Снимок другого клиента или отсутствует")
            cur.execute("""INSERT INTO public.autobid_policies
                (revision,client,marketplace,account,campaign,sku,placement,unit,spec)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (revision) DO NOTHING""",
                (policy.revision, *(obj[k] for k in ("client","marketplace","account","campaign","sku","placement","unit")), Json(spec)))
            cur.execute("""SELECT spec,client,marketplace,account,campaign,sku,placement,unit
                FROM public.autobid_policies WHERE revision=%s AND client=%s""", (policy.revision, self.client))
            stored = cur.fetchone()
            fields = ("client","marketplace","account","campaign","sku","placement","unit")
            if not stored or stored["spec"] != spec or any(stored[k] != obj[k] for k in fields):
                raise ValueError("Конфликт версии политики")
        return policy.revision

    def save_decision(self, snapshot_id: int, decision: dict) -> str:
        if decision.get("status") != "proposal" or decision.get("proposed_bid") is None:
            raise ValueError("В очередь не поступает заблокированное решение")
        if decision.get("decision_id") != _fingerprint({k: v for k, v in decision.items() if k != "decision_id"}):
            raise ValueError("Хэш решения не совпадает с содержимым")
        if decision.get("object", {}).get("client") != self.client:
            raise PermissionError("Чужой клиент")
        with closing(self.conn_factory()) as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT s.client,s.marketplace,s.account,s.campaign,s.sku,s.placement,s.unit,s.metrics,
                s.coverage,s.maturity,s.captured_at,s.source,p.spec,p.revision FROM public.autobid_snapshots s JOIN public.autobid_policies p
                ON p.revision=%s AND (p.client,p.marketplace,p.account,p.campaign,p.sku,p.placement,p.unit)=
                (s.client,s.marketplace,s.account,s.campaign,s.sku,s.placement,s.unit)
                WHERE s.id=%s AND s.client=%s""", (decision["policy_revision"], snapshot_id, self.client))
            row = cur.fetchone()
            if not row or any(decision["object"].get(k) != row[k] for k in ("client","marketplace","account","campaign","sku","placement","unit")):
                raise PermissionError("Решение не относится к снимку и версии политики")
            if decision["current_bid"] != row["metrics"].get("current_bid"):
                raise ValueError("Изменился снимок ставки")
            if decision.get("snapshot_at") != row["captured_at"].isoformat() or decision.get("source") != row["source"]:
                raise ValueError("Решение ссылается на другой снимок")
            bid = decision["proposed_bid"]
            current = decision["current_bid"]
            spec = row["spec"]
            if (type(bid) is not int or type(current) is not int or row["coverage"] != "complete"
                    or not row["maturity"] or row["metrics"].get("stock") is None
                    or row["metrics"]["stock"] <= 0
                    or not int(spec["min_bid"]) <= bid <= int(spec["max_bid"])
                    or abs(bid - current) > int(spec["max_step"])):
                raise ValueError("Решение нарушает ограничения риска")
            cur.execute("""INSERT INTO public.autobid_decisions
                (id,snapshot_id,policy_revision,current_bid,proposed_bid,unit,reason_codes,status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'proposal') ON CONFLICT (id) DO NOTHING""",
                (decision["decision_id"], snapshot_id, decision["policy_revision"], decision["current_bid"],
                 decision["proposed_bid"], row["unit"], Json(decision["reason_codes"])))
            cur.execute("SELECT snapshot_id,proposed_bid FROM public.autobid_decisions WHERE id=%s", (decision["decision_id"],))
            stored = cur.fetchone()
            if stored["snapshot_id"] != snapshot_id or stored["proposed_bid"] != decision["proposed_bid"]:
                raise ValueError("Конфликт повторного решения")
        return decision["decision_id"]

    def approve_and_enqueue(self, decision_id: str, *, actor: str, scope: dict, expires_at: datetime) -> int:
        """Database transaction only; no network send. Caller must authenticate the actor."""
        if not actor or expires_at.tzinfo is None or scope.get("client") != self.client:
            raise PermissionError("Нет точного согласования")
        with closing(self.conn_factory()) as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT d.proposed_bid,d.status,s.client,s.marketplace,s.account,s.campaign,s.sku,s.placement,s.unit
                FROM public.autobid_decisions d JOIN public.autobid_snapshots s ON s.id=d.snapshot_id
                WHERE d.id=%s AND s.client=%s FOR UPDATE OF d""", (decision_id, self.client))
            row = cur.fetchone()
            if not row or row["status"] != "proposal": raise PermissionError("Решение недоступно")
            required = ("client","marketplace","account","campaign","sku","placement","unit")
            if any(scope.get(k) != row[k] for k in required) or scope.get("bid") != row["proposed_bid"]:
                raise PermissionError("Согласование не совпадает с объектом и ставкой")
            cur.execute("SELECT now() AS server_now")
            if expires_at <= cur.fetchone()["server_now"]: raise ValueError("Согласование истекло")
            cur.execute("""INSERT INTO public.autobid_approvals(decision_id,actor,scope,expires_at)
                VALUES (%s,%s,%s,%s) ON CONFLICT (decision_id) DO NOTHING RETURNING id""",
                (decision_id, actor, Json(scope), expires_at))
            approval = cur.fetchone()
            if not approval: raise ValueError("Решение уже согласовано")
            cur.execute("""INSERT INTO public.autobid_outbox(decision_id,approval_id,state)
                VALUES (%s,%s,'queued') RETURNING id""", (decision_id, approval["id"]))
            return cur.fetchone()["id"]

    def activate_policy(self, revision: str) -> None:
        """No production route calls this until policy UI, migration and access checks exist."""
        with closing(self.conn_factory()) as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT p.* FROM public.autobid_policies p JOIN public.autobid_objects o
                ON (o.client,o.marketplace,o.account,o.campaign,o.sku,o.placement,o.unit)=
                (p.client,p.marketplace,p.account,p.campaign,p.sku,p.placement,p.unit)
                WHERE p.revision=%s AND p.client=%s FOR UPDATE OF o""", (revision, self.client))
            p = cur.fetchone()
            if not p: raise PermissionError("Политика недоступна")
            fields = (p[k] for k in ("client","marketplace","account","campaign","sku","placement","unit"))
            cur.execute("""UPDATE public.autobid_policies SET active=false
                WHERE (client,marketplace,account,campaign,sku,placement,unit)=(%s,%s,%s,%s,%s,%s,%s)
                AND active""", tuple(fields))
            cur.execute("UPDATE public.autobid_policies SET active=true WHERE revision=%s AND client=%s", (revision, self.client))

    def pause(self, account: str, *, actor: str, reason: str) -> None:
        if not account or not actor or not reason: raise ValueError("Укажите аккаунт, оператора и причину")
        with closing(self.conn_factory()) as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""INSERT INTO public.autobid_pause_events(client,account,actor,paused,reason)
                VALUES (%s,%s,%s,true,%s)""", (self.client, account, actor, reason))
            cur.execute("""UPDATE public.autobid_outbox o SET state='blocked'
                FROM public.autobid_decisions d JOIN public.autobid_snapshots s ON s.id=d.snapshot_id
                WHERE o.decision_id=d.id AND s.client=%s AND s.account=%s AND o.state='queued'""",
                (self.client, account))

    def claim(self, outbox_id: int, *, observed_bid: int, observed_stock: int, now: datetime) -> int:
        """Preflight; caller must supply a fresh, independently observed bid/stock."""
        if now.tzinfo is None or type(observed_bid) is not int or type(observed_stock) is not int:
            raise ValueError("Нет свежего наблюдения")
        with closing(self.conn_factory()) as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT o.id,o.state,o.lease_token,d.current_bid,d.proposed_bid,
                s.client,s.marketplace,s.account,s.campaign,s.sku,s.placement,s.unit,s.captured_at,s.coverage,s.maturity,
                p.active,p.spec,a.expires_at,a.revoked_at,a.scope
                FROM public.autobid_outbox o JOIN public.autobid_decisions d ON d.id=o.decision_id
                JOIN public.autobid_snapshots s ON s.id=d.snapshot_id
                JOIN public.autobid_policies p ON p.revision=d.policy_revision
                JOIN public.autobid_approvals a ON a.id=o.approval_id
                WHERE o.id=%s AND s.client=%s FOR UPDATE OF o""", (outbox_id, self.client))
            row = cur.fetchone()
            if not row: raise PermissionError("Очередь другого клиента или отсутствует")
            if row["state"] != "queued": raise RuntimeError("Команда уже обработана")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (row["client"] + ":" + row["account"],))
            cur.execute("""SELECT count(*) AS running FROM public.autobid_outbox o
                JOIN public.autobid_decisions d ON d.id=o.decision_id
                JOIN public.autobid_snapshots s ON s.id=d.snapshot_id
                WHERE s.client=%s AND s.account=%s AND o.state='sending'""", (row["client"], row["account"]))
            if cur.fetchone()["running"]: raise RuntimeError("Аккаунт уже обрабатывается")
            cur.execute("""SELECT paused FROM public.autobid_pause_events
                WHERE client=%s AND account=%s ORDER BY created_at DESC,id DESC LIMIT 1""", (row["client"], row["account"]))
            pause = cur.fetchone()
            if pause and pause["paused"]: raise PermissionError("Автобиддер приостановлен")
            scope = row["scope"]
            identity = ("client","marketplace","account","campaign","sku","placement","unit")
            if (not row["active"] or row["revoked_at"] or row["expires_at"] <= now
                    or any(scope.get(k) != row[k] for k in identity)
                    or scope.get("bid") != row["proposed_bid"]):
                raise PermissionError("Политика или согласование больше не действует")
            age = (now - row["captured_at"]).total_seconds()
            if (age < 0 or age > int(row["spec"]["max_age_seconds"]) or row["coverage"] != "complete"
                    or not row["maturity"] or observed_stock <= 0):
                raise RuntimeError("Неполный или устаревший снимок")
            if observed_bid != row["current_bid"]:
                cur.execute("UPDATE public.autobid_outbox SET state='conflict' WHERE id=%s", (outbox_id,))
                return 0
            cur.execute("""UPDATE public.autobid_outbox SET state='sending',lease_token=lease_token+1,
                lease_until=%s,attempts=attempts+1 WHERE id=%s RETURNING lease_token""",
                (now+timedelta(seconds=30), outbox_id))
            return cur.fetchone()["lease_token"]

    def mark_sent(self, outbox_id: int, token: int, *, uncertain: bool, now: datetime) -> None:
        """HTTP 200 only means awaiting readback; timeout means uncertain."""
        with closing(self.conn_factory()) as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""UPDATE public.autobid_outbox o SET state=%s FROM public.autobid_decisions d
                JOIN public.autobid_snapshots s ON s.id=d.snapshot_id
                WHERE o.decision_id=d.id AND o.id=%s AND s.client=%s AND o.state='sending'
                AND o.lease_token=%s AND o.lease_until>%s RETURNING o.id""",
                ("uncertain" if uncertain else "awaiting_readback", outbox_id, self.client, token, now))
            if not cur.fetchone(): raise RuntimeError("Исполнитель потерял lease")
            cur.execute("""INSERT INTO public.autobid_attempts(outbox_id,lease_token,state)
                VALUES (%s,%s,%s)""", (outbox_id, token, "uncertain" if uncertain else "acknowledged"))

    def expire_inflight(self, *, now: datetime) -> int:
        with closing(self.conn_factory()) as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""UPDATE public.autobid_outbox o SET state='uncertain'
                FROM public.autobid_decisions d JOIN public.autobid_snapshots s ON s.id=d.snapshot_id
                WHERE o.decision_id=d.id AND s.client=%s AND o.state='sending'
                AND o.lease_until<=%s""", (self.client, now))
            return cur.rowcount

    def readback(self, outbox_id: int, *, observed_bid: int | None, unit: str, source_at: datetime | None) -> str:
        with closing(self.conn_factory()) as conn, conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT o.state,d.proposed_bid,s.unit FROM public.autobid_outbox o
                JOIN public.autobid_decisions d ON d.id=o.decision_id
                JOIN public.autobid_snapshots s ON s.id=d.snapshot_id
                WHERE o.id=%s AND s.client=%s FOR UPDATE OF o""", (outbox_id, self.client))
            row = cur.fetchone()
            if not row or row["state"] not in ("awaiting_readback", "uncertain") or row["unit"] != unit:
                raise RuntimeError("Нет ожидающего подтверждения с этой единицей")
            if observed_bid is None:
                # Missing from the read API is not a confirmed zero or a failed write.
                return "uncertain"
            state = "confirmed" if observed_bid == row["proposed_bid"] else "conflict"
            cur.execute("""INSERT INTO public.autobid_readbacks(outbox_id,observed_bid,unit,source_at,matched)
                VALUES (%s,%s,%s,%s,%s)""", (outbox_id, observed_bid, unit, source_at, state == "confirmed"))
            cur.execute("UPDATE public.autobid_outbox SET state=%s WHERE id=%s", (state, outbox_id))
            return state
