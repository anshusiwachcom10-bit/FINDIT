import os
import sqlite3
from flask import current_app, g

from .schema import SCHEMA


def get_db():
    if "db" not in g:
        database_path = current_app.config["DATABASE_PATH"]

        if database_path != ":memory:":
            os.makedirs(os.path.dirname(database_path), exist_ok=True)

        g.db = sqlite3.connect(database_path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")

    return g.db


def close_db(exception=None):
    db = g.pop("db", None)

    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    db.commit()


def query_db(query, args=(), one=False):
    cursor = get_db().execute(query, args)
    rows = cursor.fetchall()
    cursor.close()

    if one:
        return rows[0] if rows else None

    return rows


def execute_db(query, args=()):
    db = get_db()
    cursor = db.execute(query, args)
    db.commit()
    lastrowid = cursor.lastrowid
    cursor.close()
    return lastrowid
