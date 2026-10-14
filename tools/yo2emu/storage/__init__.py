"""Almacén del emulador YO2: historial de eventos encadenado en SQLite/SQLCipher (ver NOTES.md)."""
from .backend import Store, ChainError, open_store, default_db_path  # noqa: F401
