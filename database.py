import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'traces.db')

def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address TEXT UNIQUE NOT NULL,
            label TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            hops INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            data TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS known_wallets (
            address TEXT PRIMARY KEY,
            label TEXT DEFAULT '',
            group_name TEXT DEFAULT 'intermediary',
            color TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_traces_address ON traces(wallet_address);
        CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status);
    """)
    conn.commit()
    conn.close()

def save_trace(address, data, status='completed', hops=1, label=''):
    conn = get_conn()
    conn.execute("""
        INSERT INTO traces (wallet_address, label, status, hops, data, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(wallet_address) DO UPDATE SET
            label=excluded.label,
            status=excluded.status,
            hops=excluded.hops,
            data=excluded.data,
            updated_at=excluded.updated_at
    """, (address, label, status, hops, json.dumps(data)))
    conn.commit()
    conn.close()

def get_trace(address):
    conn = get_conn()
    row = conn.execute("SELECT * FROM traces WHERE wallet_address = ?", (address,)).fetchone()
    conn.close()
    if row:
        result = dict(row)
        result['data'] = json.loads(result['data']) if result['data'] else {}
        return result
    return None

def get_all_traces():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM traces ORDER BY created_at DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if d.get('data'):
            d['data'] = json.loads(d['data']) if isinstance(d['data'], str) else d['data']
        result.append(d)
    return result

def delete_trace(address):
    conn = get_conn()
    conn.execute("DELETE FROM traces WHERE wallet_address = ?", (address,))
    conn.commit()
    conn.close()

def save_known_wallet(address, label, group_name='intermediary', color=''):
    conn = get_conn()
    conn.execute("""
        INSERT INTO known_wallets (address, label, group_name, color)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(address) DO UPDATE SET
            label=excluded.label,
            group_name=excluded.group_name,
            color=excluded.color
    """, (address, label, group_name, color))
    conn.commit()
    conn.close()

def get_known_wallets():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM known_wallets ORDER BY group_name, label").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_known_wallet(address):
    conn = get_conn()
    conn.execute("DELETE FROM known_wallets WHERE address = ?", (address,))
    conn.commit()
    conn.close()
