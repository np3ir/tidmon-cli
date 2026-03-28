import sqlite3
import logging
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
from tidmon.core.models.resources import Album

logger = logging.getLogger(__name__)


class Database:
    """SQLite database manager for tidmon"""
    
    def __init__(self):
        from tidmon.core.utils.startup import get_db_file
        self.db_file = get_db_file()
        self.connection = None
        self._init_database()
    
    def _init_database(self):
        """Initialize database with required tables"""
        try:
            self.connection = sqlite3.connect(self.db_file)
            self.connection.row_factory = sqlite3.Row
            cursor = self.connection.cursor()
            
            # Artists table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS artists (
                    artist_id INTEGER PRIMARY KEY,
                    artist_name TEXT NOT NULL,
                    added_date TEXT NOT NULL,
                    last_checked TEXT,
                    active INTEGER DEFAULT 1
                )
            ''')
            
            # Albums table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS albums (
                    album_id INTEGER PRIMARY KEY,
                    artist_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    release_date TEXT,
                    album_type TEXT,
                    explicit INTEGER DEFAULT 0,
                    number_of_tracks INTEGER,
                    added_date TEXT NOT NULL,
                    notified INTEGER DEFAULT 0,
                    downloaded INTEGER DEFAULT 0,
                    FOREIGN KEY (artist_id) REFERENCES artists (artist_id)
                )
            ''')
            
            # Releases tracking table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_id INTEGER NOT NULL,
                    artist_id INTEGER NOT NULL,
                    detected_date TEXT NOT NULL,
                    FOREIGN KEY (album_id) REFERENCES albums (album_id),
                    FOREIGN KEY (artist_id) REFERENCES artists (artist_id)
                )
            ''')

            # Junction table: many-to-many artist <-> album ownership
            # Fixes INSERT OR REPLACE overwriting artist_id on shared albums
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS artist_albums (
                    artist_id INTEGER NOT NULL,
                    album_id  INTEGER NOT NULL,
                    PRIMARY KEY (artist_id, album_id),
                    FOREIGN KEY (artist_id) REFERENCES artists (artist_id),
                    FOREIGN KEY (album_id)  REFERENCES albums  (album_id)
                )
            ''')
            
            # Configuration table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            
            # Tables for playlist monitoring
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS monitored_playlists (
                    uuid TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    added_date TEXT NOT NULL,
                    last_checked TEXT
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS playlist_tracks (
                    playlist_uuid TEXT NOT NULL,
                    track_id INTEGER NOT NULL,
                    PRIMARY KEY (playlist_uuid, track_id),
                    FOREIGN KEY (playlist_uuid) REFERENCES monitored_playlists (uuid) ON DELETE CASCADE
                )
            ''')
            
            self._migrate_schema(cursor)
            
            self.connection.commit()
            logger.debug(f"Database initialized at {self.db_file}")
        
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}")

    def _migrate_schema(self, cursor):
        """Check for and apply necessary database schema migrations."""
        try:
            # Migration 1: 'downloaded' column
            cursor.execute("PRAGMA table_info(albums)")
            columns = [col['name'] for col in cursor.fetchall()]
            if 'downloaded' not in columns:
                logger.info("Migrating database: Adding 'downloaded' column to albums table.")
                cursor.execute('ALTER TABLE albums ADD COLUMN downloaded INTEGER DEFAULT 0')

            # Migration 2: populate artist_albums junction table from existing albums rows
            cursor.execute("SELECT COUNT(*) as cnt FROM artist_albums")
            if cursor.fetchone()['cnt'] == 0:
                logger.info("Migrating database: Populating artist_albums junction table.")
                cursor.execute('''
                    INSERT OR IGNORE INTO artist_albums (artist_id, album_id)
                    SELECT artist_id, album_id FROM albums
                ''')
                logger.info("Migration complete: artist_albums populated.")

            # Migration 3: album_artist_name column
            if 'album_artist_name' not in columns:
                logger.info("Migrating database: Adding 'album_artist_name' column to albums table.")
                cursor.execute("ALTER TABLE albums ADD COLUMN album_artist_name TEXT")

        except sqlite3.Error as e:
            logger.error(f"Database migration error: {e}")
    
    def add_artist(self, artist_id: int, artist_name: str) -> bool:
        """Add an artist to monitoring"""
        try:
            cursor = self.connection.cursor()
            added_date = datetime.now().isoformat()
            
            cursor.execute('''
                INSERT OR REPLACE INTO artists (artist_id, artist_name, added_date)
                VALUES (?, ?, ?)
            ''', (artist_id, artist_name, added_date))
            
            self.connection.commit()
            logger.info(f"Added artist: {artist_name} (ID: {artist_id})")
            return True
        
        except sqlite3.Error as e:
            logger.error(f"Failed to add artist: {e}")
            return False
    
    def remove_artist(self, artist_id: int) -> bool:
        """Remove an artist and all their associated data from monitoring"""
        try:
            cursor = self.connection.cursor()
            cursor.execute('DELETE FROM releases WHERE artist_id = ?', (artist_id,))
            cursor.execute('DELETE FROM artist_albums WHERE artist_id = ?', (artist_id,))
            # Only delete orphan albums (not shared with other artists)
            cursor.execute('''
                DELETE FROM albums
                WHERE artist_id = ?
                  AND album_id NOT IN (SELECT album_id FROM artist_albums)
            ''', (artist_id,))
            cursor.execute('DELETE FROM artists WHERE artist_id = ?', (artist_id,))
            self.connection.commit()
            logger.info(f"Removed artist ID: {artist_id} and all associated data.")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to remove artist: {e}")
            return False
    
    def get_artist(self, artist_id: int) -> Optional[Dict]:
        """Get artist by ID"""
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM artists WHERE artist_id = ?', (artist_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"Failed to get artist: {e}")
            return None
    
    def get_artist_by_name(self, artist_name: str) -> Optional[Dict]:
        """Get artist by name"""
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM artists WHERE artist_name LIKE ?', (f'%{artist_name}%',))
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"Failed to get artist: {e}")
            return None
    
    def get_all_artists(self, since: Optional[str] = None, until: Optional[str] = None) -> List[Dict]:
        """Get all monitored artists, with optional date filters on when they were added."""
        try:
            cursor = self.connection.cursor()
            query = "SELECT * FROM artists WHERE active = 1"
            params = []

            if since:
                query += " AND date(added_date) >= ?"
                params.append(since)

            if until:
                query += " AND date(added_date) <= ?"
                params.append(until)

            query += " ORDER BY artist_name"

            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to get artists: {e}")
            return []

    # --- Playlist Monitoring Methods ---

    def add_playlist(self, uuid: str, name: str) -> bool:
        """Add a playlist to monitoring"""
        try:
            cursor = self.connection.cursor()
            added_date = datetime.now().isoformat()
            cursor.execute('''
                INSERT OR REPLACE INTO monitored_playlists (uuid, name, added_date)
                VALUES (?, ?, ?)
            ''', (uuid, name, added_date))
            self.connection.commit()
            logger.info(f"Added playlist to monitoring: {name} (ID: {uuid})")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to add playlist: {e}")
            return False

    def remove_playlist(self, uuid: str) -> bool:
        """Remove a playlist from monitoring"""
        try:
            cursor = self.connection.cursor()
            # The ON DELETE CASCADE will handle cleaning up playlist_tracks
            cursor.execute('DELETE FROM monitored_playlists WHERE uuid = ?', (uuid,))
            self.connection.commit()
            logger.info(f"Removed playlist from monitoring: {uuid}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to remove playlist: {e}")
            return False

    def get_monitored_playlists(self) -> List[Dict]:
        """Get all monitored playlists"""
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM monitored_playlists ORDER BY name')
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to get monitored playlists: {e}")
            return []

    def get_playlist_track_ids(self, playlist_uuid: str) -> set[int]:
        """Get all track IDs for a given playlist"""
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT track_id FROM playlist_tracks WHERE playlist_uuid = ?', (playlist_uuid,))
            rows = cursor.fetchall()
            return {row['track_id'] for row in rows}
        except sqlite3.Error as e:
            logger.error(f"Failed to get playlist track IDs: {e}")
            return set()

    def update_playlist_tracks(self, playlist_uuid: str, track_ids: set[int]) -> bool:
        """Update the stored tracks for a monitored playlist"""
        try:
            cursor = self.connection.cursor()
            # Clear old tracks for the playlist
            cursor.execute('DELETE FROM playlist_tracks WHERE playlist_uuid = ?', (playlist_uuid,))
            
            # Insert new tracks
            if track_ids:
                new_tracks_data = [(playlist_uuid, track_id) for track_id in track_ids]
                cursor.executemany('INSERT INTO playlist_tracks (playlist_uuid, track_id) VALUES (?, ?)', new_tracks_data)
            
            self.connection.commit()
            self.update_playlist_check_time(playlist_uuid) # Also update the check time
            logger.debug(f"Updated {len(track_ids)} tracks for playlist {playlist_uuid}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to update playlist tracks: {e}")
            return False

    def update_playlist_check_time(self, playlist_uuid: str):
        """Update last checked time for a playlist"""
        try:
            cursor = self.connection.cursor()
            check_time = datetime.now().isoformat()
            cursor.execute('''
                UPDATE monitored_playlists SET last_checked = ? WHERE uuid = ?
            ''', (check_time, playlist_uuid))
            self.connection.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to update playlist check time: {e}")

    
    def update_artist_check_time(self, artist_id: int):
        """Update last checked time for artist"""
        try:
            cursor = self.connection.cursor()
            check_time = datetime.now().isoformat()
            cursor.execute('''
                UPDATE artists SET last_checked = ? WHERE artist_id = ?
            ''', (check_time, artist_id))
            self.connection.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to update check time: {e}")
    
    def add_album(self, album: Album, artist_id: int) -> bool:
        """Add an album to the database.

        Uses INSERT OR IGNORE so shared albums (features, compilations) are
        never overwritten by a different artist_id.  The artist_albums junction
        table tracks every artist that claims the album, so get_artist_albums()
        works correctly for all artists regardless of who inserted the row first.
        """
        try:
            cursor = self.connection.cursor()
            added_date = datetime.now().isoformat()
            release_date_str = album.release_date.isoformat() if album.release_date else None

            album_artist_name = getattr(album.artist, 'name', None) if album.artist else None

            # 1. Insert album row only if it doesn't exist yet (no overwrite)
            cursor.execute('''
                INSERT OR IGNORE INTO albums
                (album_id, artist_id, title, release_date, album_type,
                 explicit, number_of_tracks, added_date, album_artist_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                album.id,
                artist_id,
                album.title,
                release_date_str,
                album.type,
                album.explicit,
                album.number_of_tracks,
                added_date,
                album_artist_name,
            ))

            # 2. Always register the artist-album relationship
            cursor.execute('''
                INSERT OR IGNORE INTO artist_albums (artist_id, album_id)
                VALUES (?, ?)
            ''', (artist_id, album.id))

            self.connection.commit()
            return True

        except sqlite3.Error as e:
            logger.error(f"Failed to add album: {e}")
            return False
    
    def get_album(self, album_id: int) -> Optional[Dict]:
        """Get album by ID"""
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM albums WHERE album_id = ?', (album_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"Failed to get album: {e}")
            return None
    
    def get_artist_albums(self, artist_id: int) -> List[Dict]:
        """Get all albums for an artist via the artist_albums junction table.

        Falls back to the legacy artist_id column for rows that pre-date the
        junction table migration (shouldn't happen after first run, but safe).
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT al.*
                FROM albums al
                JOIN artist_albums aa ON al.album_id = aa.album_id
                WHERE aa.artist_id = ?
                ORDER BY al.release_date DESC
            ''', (artist_id,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to get albums: {e}")
            return []

    def get_albums(self, artist_id: Optional[int] = None, since: Optional[str] = None, until: Optional[str] = None, include_downloaded: bool = False) -> List[Dict]:
        """
        Get albums, with optional filters for artist, release date, and download status.

        Args:
            artist_id: If provided, only get albums for this artist.
            since: If provided, only get albums released on or after this date (YYYY-MM-DD).
            until: If provided, only get albums released on or before this date (YYYY-MM-DD).
            include_downloaded: If True, include all albums regardless of download status.
        """
        try:
            cursor = self.connection.cursor()

            query_parts = ["SELECT a.*, ar.artist_name FROM albums a JOIN artists ar ON a.artist_id = ar.artist_id"]
            where_clauses = []
            params = []

            if not include_downloaded:
                where_clauses.append("a.downloaded = 0")

            if artist_id:
                where_clauses.append("a.artist_id = ?")
                params.append(artist_id)

            if since:
                where_clauses.append("a.release_date >= ?")
                params.append(since)

            if until:
                where_clauses.append("a.release_date <= ?")
                params.append(until)

            if where_clauses:
                query_parts.append("WHERE " + " AND ".join(where_clauses))

            query_parts.append("ORDER BY ar.artist_name, a.release_date DESC")

            query = " ".join(query_parts)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to get albums: {e}")
            return []

    def mark_album_as_downloaded(self, album_id: int):
        """Mark an album as downloaded."""
        try:
            cursor = self.connection.cursor()
            cursor.execute('UPDATE albums SET downloaded = 1 WHERE album_id = ?', (album_id,))
            self.connection.commit()
            logger.debug(f"Marked album {album_id} as downloaded.")
        except sqlite3.Error as e:
            logger.error(f"Failed to mark album {album_id} as downloaded: {e}")

    
    def get_recent_releases(self, days: int = 7) -> List[Dict]:
        """Get recent releases within specified days"""
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT a.*, ar.artist_name 
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.artist_id
                WHERE date(a.release_date) >= date('now', '-' || ? || ' days')
                ORDER BY a.release_date DESC
            ''', (days,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to get recent releases: {e}")
            return []
    
    def get_future_releases(self) -> List[Dict]:
        """Get future/upcoming releases"""
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT a.*, ar.artist_name 
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.artist_id
                WHERE date(a.release_date) > date('now')
                ORDER BY a.release_date ASC
            ''')
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Failed to get future releases: {e}")
            return []
    
    def clear_artists(self) -> bool:
        """Removes all artists and their associated data (albums, releases)."""
        try:
            cursor = self.connection.cursor()
            # Must delete from child tables first due to foreign key constraints
            cursor.execute('DELETE FROM releases')
            cursor.execute('DELETE FROM albums')
            cursor.execute('DELETE FROM artists')
            self.connection.commit()
            logger.info("Cleared all artists and associated data.")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to clear artists: {e}")
            return False

    def clear_playlists(self) -> bool:
        """Removes all monitored playlists."""
        try:
            cursor = self.connection.cursor()
            # ON DELETE CASCADE on playlist_tracks will handle associated tracks
            cursor.execute('DELETE FROM monitored_playlists')
            self.connection.commit()
            logger.info("Cleared all monitored playlists.")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to clear playlists: {e}")
            return False
    
    def close(self) -> None:
        """Close the database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.debug("Database connection closed")

    # ── Context manager support ───────────────────────────────────────────────

    def __enter__(self) -> "Database":
        """Allow usage as: with Database() as db: ..."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close the connection automatically when exiting the with block."""
        self.close()

    def __del__(self) -> None:
        """Safety net: close the connection if the object is garbage collected."""
        self.close()


    def get_artist_stats(self) -> list:
        """Returns per-artist stats: total release count (all types) and total tracks."""
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT ar.artist_id, ar.artist_name,
                       COUNT(a.album_id) AS releases,
                       COALESCE(SUM(a.number_of_tracks), 0) AS total_tracks
                FROM artists ar
                LEFT JOIN albums a ON ar.artist_id = a.artist_id
                WHERE ar.active = 1
                GROUP BY ar.artist_id, ar.artist_name
                ORDER BY ar.artist_name
            ''')
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get artist stats: {e}")
            return []

    def get_album_counts_per_artist(self) -> dict:
        """Returns {artist_id: album_count} for all artists in one query."""
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT artist_id, COUNT(*) as cnt
                FROM albums
                GROUP BY artist_id
            ''')
            return {row["artist_id"]: row["cnt"] for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Failed to get album counts: {e}")
            return {}