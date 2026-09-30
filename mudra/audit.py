"""Edit log (audit trail) — every create/alter/delete is recorded."""
import json


def log(conn, company_id, action, entity, entity_id, summary, snapshot=None):
    conn.execute(
        "INSERT INTO edit_log(company_id, action, entity, entity_id, summary, snapshot)"
        " VALUES (?,?,?,?,?,?)",
        (
            company_id,
            action,
            entity,
            entity_id,
            summary,
            json.dumps(snapshot, default=str) if snapshot is not None else None,
        ),
    )


def list_log(conn, company_id, limit=200, entity=None):
    sql = "SELECT * FROM edit_log WHERE company_id = ?"
    args = [company_id]
    if entity:
        sql += " AND entity = ?"
        args.append(entity)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    rows = []
    for r in conn.execute(sql, args):
        d = dict(r)
        d["snapshot"] = json.loads(d["snapshot"]) if d["snapshot"] else None
        rows.append(d)
    return rows
