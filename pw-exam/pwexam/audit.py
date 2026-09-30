"""Audit log of every administrative and exam action."""
import json

from . import clock


def log(conn, actor, action, entity=None, entity_id=None, detail=None, ip=None):
    conn.execute(
        "INSERT INTO audit_log(ts, actor_id, actor, action, entity, entity_id, detail, ip) VALUES (?,?,?,?,?,?,?,?)",
        (clock.now(), actor.get("id") if actor else None, actor.get("login_id") if actor else "system",
         action, entity, entity_id,
         json.dumps(detail, default=str) if isinstance(detail, (dict, list)) else detail, ip),
    )


def list_log(conn, limit=300, entity=None):
    sql, args = "SELECT * FROM audit_log", []
    if entity:
        sql += " WHERE entity = ?"
        args.append(entity)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(min(int(limit), 2000))
    return [dict(r) for r in conn.execute(sql, args)]
