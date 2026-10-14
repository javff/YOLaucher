"""Esquema y migraciones versionadas. SQL estándar (TEXT, INTEGER, BIGINT, SMALLINT, claves primarias compuestas):
vale igual en SQLite y PostgreSQL. La versión se guarda con adapter.set_schema_version (en SQLite, PRAGMA user_version).

Cada migración: (versión, descripción, [sentencias], ¿reconstruir tablas de estado después?). Para cambiar el
esquema se AÑADE una entrada al final; las viejas no se tocan.
"""

PROJECTION_TABLES = ("accounts", "characters", "trunk", "decks", "npc_progress", "friends", "titles",
                     "mail", "teams", "kv")

V1 = [
    # Historial: solo crece. hash = sha256 encadenado (chain.event_hash). sig: firma ed25519 futura (vacía).
    """CREATE TABLE events (
        seq BIGINT PRIMARY KEY,
        ts TEXT NOT NULL,
        acct TEXT,
        slot TEXT,
        kind TEXT NOT NULL,
        data TEXT NOT NULL,
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL UNIQUE,
        sig TEXT
    )""",
    "CREATE INDEX events_acct ON events (acct, seq)",
    # Foto completa del estado cada N eventos: arranque rápido (foto + eventos posteriores). Derivada.
    """CREATE TABLE snapshots (
        seq BIGINT PRIMARY KEY,
        event_hash TEXT NOT NULL,
        ts TEXT NOT NULL,
        state TEXT NOT NULL
    )""",
    # ---- tablas de estado (derivadas del historial; reconstruibles con «python -m storage reconstruir»)
    """CREATE TABLE accounts (
        acct TEXT PRIMARY KEY,
        is_owner SMALLINT NOT NULL,
        idx INTEGER,
        active_slot INTEGER,
        data TEXT NOT NULL
    )""",
    """CREATE TABLE characters (
        acct TEXT NOT NULL,
        slot TEXT NOT NULL,
        name TEXT,
        lv INTEGER,
        mp BIGINT,
        dpp BIGINT,
        title INTEGER,
        player_id BIGINT,
        data TEXT NOT NULL,
        PRIMARY KEY (acct, slot)
    )""",
    """CREATE TABLE trunk (
        acct TEXT NOT NULL,
        slot TEXT NOT NULL,
        card TEXT NOT NULL,
        qty INTEGER,
        raw TEXT,
        PRIMARY KEY (acct, slot, card)
    )""",
    """CREATE TABLE decks (
        acct TEXT NOT NULL,
        slot TEXT NOT NULL,
        name TEXT NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY (acct, slot, name)
    )""",
    """CREATE TABLE npc_progress (
        acct TEXT NOT NULL,
        slot TEXT NOT NULL,
        npc TEXT NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY (acct, slot, npc)
    )""",
    """CREATE TABLE friends (
        acct TEXT NOT NULL,
        slot TEXT NOT NULL,
        pos INTEGER NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (acct, slot, pos)
    )""",
    """CREATE TABLE titles (
        acct TEXT NOT NULL,
        slot TEXT NOT NULL,
        pbin TEXT NOT NULL,
        PRIMARY KEY (acct, slot)
    )""",
    """CREATE TABLE mail (
        pos INTEGER PRIMARY KEY,
        mail_id BIGINT,
        from_pid BIGINT,
        to_pid BIGINT,
        data TEXT NOT NULL
    )""",
    """CREATE TABLE teams (
        team_id TEXT PRIMARY KEY,
        data TEXT NOT NULL
    )""",
    """CREATE TABLE kv (
        path TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
]

MIGRATIONS = [
    (1, "esquema inicial: events + snapshots + tablas de estado", V1, False),
]
LATEST = MIGRATIONS[-1][0]


def migrate(ad, log=None):
    """Aplica las migraciones pendientes, cada una en su transacción. Devuelve True si alguna pide reconstruir."""
    log = log or (lambda *a: None)
    cur = ad.get_schema_version()
    if cur > LATEST:
        raise RuntimeError(f"la base está en el esquema {cur} y este código solo conoce hasta el {LATEST}")
    rebuild = False
    for n, desc, stmts, rb in MIGRATIONS:
        if n <= cur:
            continue
        ad.begin_write()
        try:
            for s in stmts:
                ad.execute(s)
            ad.set_schema_version(n)
            ad.commit()
        except Exception:
            ad.rollback()
            raise
        log(f"[almacén] esquema {n}: {desc}")
        rebuild = rebuild or rb
    return rebuild
