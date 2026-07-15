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

    # subStatus 7004 = "Cannot have more than 10000 favorites" (tope duro de TIDAL)
    FAVORITES_CAP_SUBSTATUS = 7004

    def _post_chunk(self, kind: str, field: str, ids: list) -> str:
        """POST un lote de IDs a favorites/{kind}.
        Devuelve 'ok' | 'fail' | 'cap' (tope de 10000 alcanzado → abortar)."""
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
        if r.ok:
            return "ok"
        # ¿Tope de 10000 favoritos? No tiene sentido seguir mandando lotes.
        try:
            if r.json().get("subStatus") == self.FAVORITES_CAP_SUBSTATUS:
                return "cap"
        except Exception:
            pass
        logger.error("POST favorites/%s falló: %s %s", kind, r.status_code, r.text[:150])
        return "fail"

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
        capped = False
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                      BarColumn(), TextColumn("{task.completed}/{task.total} lotes"),
                      TimeElapsedColumn(), console=console) as prog:
            task = prog.add_task(f"Añadiendo {label}", total=len(chunks))
            for chunk in chunks:
                result = self._post_chunk(kind, get_field, chunk)
                if result == "ok":
                    added += len(chunk)
                elif result == "cap":
                    # Tope de 10000 de TIDAL: no seguir martillando lotes que fallarán todos.
                    capped = True
                    break
                else:
                    failed += len(chunk)
                prog.advance(task)
                if pause > 0:
                    time.sleep(pause)

        msg = f"[green]OK: {label}: {added} añadidos[/]"
        if failed:
            msg += f" | [red]{failed} fallaron[/]"
        console.print(msg)
        if capped:
            console.print(
                "[bold red]Tope de TIDAL alcanzado: máximo 10,000 favoritos.[/] "
                f"No caben más {label}; el resto quedó sin añadir."
            )

    # ── resolución de artistas (id | nombre | archivo) ───────────────────────
    def _resolve_artists(self, tokens: list) -> tuple:
        """Convierte una lista de tokens (IDs numéricos o nombres) en IDs de TIDAL.
        Los nombres se resuelven contra la DB de tidmon (get_artist_by_name).
        Devuelve (ids_resueltos, no_encontrados)."""
        ids, missing = [], []
        for tok in tokens:
            tok = str(tok).strip()
            if not tok:
                continue
            if tok.isdigit():
                ids.append(int(tok))
                continue
            row = self.db.get_artist_by_name(tok)
            if row and row.get("artist_id") is not None:
                ids.append(row["artist_id"])
            else:
                missing.append(tok)
        # dedup preservando orden
        seen, out = set(), []
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out, missing

    @staticmethod
    def _read_file_tokens(path: str) -> list:
        """Lee un archivo con un artista por línea (ID o nombre). Ignora vacías y #."""
        with open(path, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]

    # ── follow / unfollow granular ───────────────────────────────────────────
    def follow(self, tokens: list, file: Optional[str], chunk_size: int, pause: float) -> None:
        toks = list(tokens)
        if file:
            toks += self._read_file_tokens(file)
        if not toks:
            console.print("[yellow]Nada que seguir. Pasa IDs/nombres o --file.[/]")
            return
        ids, missing = self._resolve_artists(toks)
        if missing:
            console.print(f"[yellow]No encontrados en la DB (se ignoran):[/] {', '.join(missing)}")
        if not ids:
            console.print("[red]Ningún artista resuelto.[/]")
            return
        self._sync("artistas", "artists", "artistIds", "id", ids, chunk_size, pause)

    def unfollow(self, tokens: list, file: Optional[str], pause: float, unfollow_all: bool) -> None:
        if unfollow_all:
            ids = sorted(int(i) for i in self._get_existing("artists", "id"))
            console.print(f"[bold red]Vas a des-seguir TODOS ({len(ids)}) los artistas.[/]")
        else:
            toks = list(tokens)
            if file:
                toks += self._read_file_tokens(file)
            if not toks:
                console.print("[yellow]Nada que des-seguir. Pasa IDs/nombres, --file, o --all.[/]")
                return
            ids, missing = self._resolve_artists(toks)
            if missing:
                console.print(f"[yellow]No encontrados en la DB (se ignoran):[/] {', '.join(missing)}")
        if not ids:
            console.print("[red]Ningún artista para des-seguir.[/]")
            return

        removed, failed = 0, 0
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                      BarColumn(), TextColumn("{task.completed}/{task.total}"),
                      TimeElapsedColumn(), console=console) as prog:
            task = prog.add_task("Des-siguiendo", total=len(ids))
            for aid in ids:
                r = requests.delete(f"{_API}/users/{self.user_id}/favorites/artists/{aid}",
                                    params={"countryCode": self.country}, headers=self.headers, timeout=20)
                if r.status_code == 401:
                    get_session().get_api()
                    self.headers["Authorization"] = f"Bearer {load_auth_data().token}"
                    r = requests.delete(f"{_API}/users/{self.user_id}/favorites/artists/{aid}",
                                        params={"countryCode": self.country}, headers=self.headers, timeout=20)
                if r.ok:
                    removed += 1
                else:
                    failed += 1
                    logger.error("DELETE artist/%s falló: %s", aid, r.status_code)
                prog.advance(task)
                if pause > 0:
                    time.sleep(pause)
        msg = f"[green]OK: {removed} des-seguidos[/]"
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
