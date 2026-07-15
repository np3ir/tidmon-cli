"""
Marca en la cuenta TIDAL del usuario, como FAVORITOS/SEGUIDOS, todos los
artistas y playlists que tidmon tiene en su base de datos local.

TIDAL acepta varios IDs por llamada (endpoint v1 `favorites/artists` con
`artistIds` separado por comas, y `favorites/playlists` con `uuids`), así que
esto NO es una escritura por artista: se manda en lotes grandes. Se saltan los
que ya están en favoritos (se leen primero) para no repetir trabajo ni escribir
de más. El progreso es reanudable: correrlo de nuevo solo añade lo que falte.
"""

import logging
import time
from typing import Optional

import requests
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from tidmon.core.auth import get_session, load_auth_data
from tidmon.core.db import Database

logger = logging.getLogger(__name__)
console = Console()

_API = "https://api.tidal.com/v1"


class Favorite:
    """Sincroniza la biblioteca de tidmon hacia los favoritos de la cuenta TIDAL."""

    def __init__(self) -> None:
        self.db = Database()
        # Refrescar el token proactivamente: son escrituras y un 401 a mitad
        # abortaría el lote. get_api dispara el refresh reactivo; además leemos
        # el token ya fresco de disco.
        get_session().get_api()
        auth = load_auth_data()
        if not auth.user_id or not auth.token:
            raise RuntimeError("No hay sesión TIDAL válida. Corre `tidmon auth` primero.")
        self.user_id = auth.user_id
        self.country = auth.country_code or "US"
        self.headers = {
            "Authorization": f"Bearer {auth.token}",
            "User-Agent": "Mozilla/5.0",
        }

    # ── helpers HTTP ─────────────────────────────────────────────────────────
    def _get_existing(self, kind: str, id_key: str) -> set:
        """Set de IDs ya presentes en favoritos (artists o playlists)."""
        existing = set()
        offset = 0
        while True:
            r = requests.get(f"{_API}/users/{self.user_id}/favorites/{kind}",
                             params={"countryCode": self.country, "limit": 50, "offset": offset},
                             headers=self.headers, timeout=20)
            if r.status_code != 200:
                logger.warning("No se pudieron leer favoritos existentes (%s): %s", kind, r.status_code)
                break
            data = r.json()
            items = data.get("items", [])
            for it in items:
                obj = it.get("item", it)
                val = obj.get(id_key)
                if val is not None:
                    existing.add(str(val))
            offset += 50
            if not items or offset >= data.get("totalNumberOfItems", 0):
                break
        return existing

    def _post_chunk(self, kind: str, field: str, ids: list) -> bool:
        """POST un lote de IDs a favorites/{kind}. True si HTTP 2xx."""
        r = requests.post(f"{_API}/users/{self.user_id}/favorites/{kind}",
                          params={"countryCode": self.country},
                          data={field: ",".join(str(i) for i in ids)},
                          headers=self.headers, timeout=30)
        if r.status_code == 401:
            # token expiró a mitad — refrescar y reintentar una vez
            get_session().get_api()
            self.headers["Authorization"] = f"Bearer {load_auth_data().token}"
            r = requests.post(f"{_API}/users/{self.user_id}/favorites/{kind}",
                              params={"countryCode": self.country},
                              data={field: ",".join(str(i) for i in ids)},
                              headers=self.headers, timeout=30)
        if not r.ok:
            logger.error("POST favorites/%s falló: %s %s", kind, r.status_code, r.text[:150])
        return r.ok

    # ── sincronización genérica ──────────────────────────────────────────────
    def _sync(self, label: str, kind: str, get_field: str, id_key: str,
              all_ids: list, chunk_size: int, pause: float) -> None:
        if not all_ids:
            console.print(f"[yellow]No hay {label} en la base de datos de tidmon.[/]")
            return

        console.print(f"[cyan]Leyendo {label} ya en favoritos...[/]")
        existing = self._get_existing(kind, id_key)
        pending = [i for i in all_ids if str(i) not in existing]

        console.print(
            f"[bold]{label}:[/] {len(all_ids)} en tidmon | "
            f"{len(existing)} ya en favoritos | [green]{len(pending)} por añadir[/]"
        )
        if not pending:
            console.print(f"[green]OK: Todos los {label} ya estaban seguidos.[/]")
            return

        chunks = [pending[i:i + chunk_size] for i in range(0, len(pending), chunk_size)]
        added = 0
        failed = 0
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                      BarColumn(), TextColumn("{task.completed}/{task.total} lotes"),
                      TimeElapsedColumn(), console=console) as prog:
            task = prog.add_task(f"Añadiendo {label}", total=len(chunks))
            for chunk in chunks:
                if self._post_chunk(kind, get_field, chunk):
                    added += len(chunk)
                else:
                    failed += len(chunk)
                prog.advance(task)
                if pause > 0:
                    time.sleep(pause)

        msg = f"[green]OK: {label}: {added} añadidos[/]"
        if failed:
            msg += f" | [red]{failed} fallaron[/]"
        console.print(msg)

    # ── entrypoints ──────────────────────────────────────────────────────────
    def sync_artists(self, chunk_size: int, pause: float) -> None:
        artists = self.db.get_all_artists()
        ids = [a["artist_id"] for a in artists if a.get("artist_id") is not None]
        self._sync("artistas", "artists", "artistIds", "id", ids, chunk_size, pause)

    def sync_playlists(self, chunk_size: int, pause: float) -> None:
        playlists = self.db.get_monitored_playlists()
        uuids = [p["uuid"] for p in playlists if p.get("uuid")]
        self._sync("playlists", "playlists", "uuids", "uuid", uuids, chunk_size, pause)

    def run(self, artists: bool, playlists: bool, chunk_size: int, pause: float) -> None:
        if artists:
            self.sync_artists(chunk_size, pause)
        if playlists:
            self.sync_playlists(chunk_size, pause)
