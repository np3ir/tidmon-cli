import logging
from pydantic import BaseModel
from tidmon.core.db import Database
from tidmon.core.config import Config
from tidmon.core.models.resources import Artist, Album
from tidmon.core.utils import url as url_utils
from tidmon.core.auth import get_session
from typing import List, Union, Optional
from tidmon.core.auth import TidalSession

logger = logging.getLogger(__name__)


class Monitor:
    """Handle artist monitoring operations"""
    
    def __init__(self, config: Config = None, session: TidalSession = None):
        self.config = config or Config()
        self.db = Database()
        self.session = session or get_session()
        self._api = None

    @property
    def api(self):
        """Lazy-loads the API client upon first access."""
        if self._api is None:
            self._api = self.session.get_api()
        return self._api
    
    def close(self) -> None:
        """Close the database connection."""
        self.db.close()

    def __enter__(self) -> "Monitor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def add_by_name(self, artist_name: str) -> bool:
        """Add artist to monitoring by name"""
        # First, check if an artist with a similar name is already monitored
        existing = self.db.get_artist_by_name(artist_name)
        if existing and existing['artist_name'].lower() == artist_name.lower():
            print(f"✅ Artist \"{existing['artist_name']}\" is already being monitored.")
            logger.info(f"Skipping API call for already-monitored artist: {artist_name}")
            return True

        logger.info(f"Searching for artist on TIDAL: {artist_name}")
        
        # Search for artist
        results = self.api.search(artist_name, "ARTISTS", limit=5)
        
        if not results or not results.artists or not results.artists.items:
            print(f"❌ No artists found for '{artist_name}' on TIDAL.")
            logger.error(f"No artists found for '{artist_name}'")
            return False
        
        # If single result, add directly
        if len(results.artists.items) == 1:
            artist = results.artists.items[0]
            return self._add_artist(artist)
        
        # Multiple results - let user choose
        print("\n📋 Multiple artists found on TIDAL:")
        for i, artist in enumerate(results.artists.items, 1):
            monitored = self.db.get_artist(artist.id)
            status = " ✓ [MONITORED]" if monitored else ""
            print(f"  {i}. {artist.name} (ID: {artist.id}){status}")
        
        while True:
            try:
                choice = input("\n👉 Select artist number (or 0 to cancel): ")
                choice = int(choice)
                
                if choice == 0:
                    logger.info("Cancelled by user.")
                    print("Action cancelled.")
                    return False
                
                if 1 <= choice <= len(results.artists.items):
                    artist = results.artists.items[choice - 1]
                    return self._add_artist(artist)
                else:
                    print("❌ Invalid choice. Please try again.")
            except ValueError:
                print("❌ Please enter a number.")
            except KeyboardInterrupt:
                print("\n❌ Action cancelled.")
                return False
    
    def add_by_id(self, artist_id: int) -> bool:
        """Add artist to monitoring by ID"""
        # First, check if artist is already monitored in the DB
        existing = self.db.get_artist(artist_id)
        if existing:
            print(f"✅ Artist \"{existing['artist_name']}\" (ID: {artist_id}) is already being monitored.")
            logger.info(f"Skipping API call for already-monitored artist ID: {artist_id}")
            return True

        logger.info(f"Artist not in DB. Getting artist info from TIDAL for ID: {artist_id}")
        
        artist = self.api.get_artist(artist_id)
        
        if not artist:
            print(f"❌ Artist with ID {artist_id} not found on TIDAL.")
            logger.error(f"Artist with ID {artist_id} not found")
            return False
        
        return self._add_artist(artist)
    

    
    def add_from_file(self, filepath: str):
        """
        Add multiple artists from a file.
        Each line can be an artist ID, URL, or name.
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except FileNotFoundError:
            logger.error(f"File not found: {filepath}")
            return
        except Exception as e:
            logger.error(f"Error reading file: {e}")
            return

        logger.info(f"Importing artists from {filepath}...")
        for line in lines:
            line = line.split('#')[0].strip()
            if not line:
                continue

            logger.info(f"Processing line: '{line}'")
            # Is it a URL?
            parsed_url = url_utils.parse_url(line)
            if parsed_url:
                if parsed_url.tidal_type == url_utils.TidalType.ARTIST:
                    logger.info(f"Found artist URL. ID: {parsed_url.tidal_id}")
                    self.add_by_id(parsed_url.tidal_id)
                    continue
                elif parsed_url.tidal_type == url_utils.TidalType.PLAYLIST:
                    logger.info(f"Found playlist URL: {line}")
                    self.add_playlist(line)
                    continue

            # Is it an ID?
            try:
                artist_id = int(line)
                logger.info(f"Found artist ID: {artist_id}")
                self.add_by_id(artist_id)
                continue
            except ValueError:
                pass

            # Treat as a name
            logger.info(f"Treating as artist name: {line}")
            self.add_by_name(line)

    
    def _add_artist(self, artist: Artist) -> bool:
        """Internal method to add artist to database"""
        # Check if already monitoring
        existing = self.db.get_artist(artist.id)
        if existing:
            logger.warning(f"Already monitoring {artist.name} (ID: {artist.id})")
            return False
        
        # Add to database
        if self.db.add_artist(artist.id, artist.name):
            logger.info(f"✓ Now monitoring: {artist.name} (ID: {artist.id})")
            
            # Fetch initial albums
            self._fetch_artist_albums(artist.id)
            
            return True
        
        return False
    
    def _fetch_artist_albums(self, artist_id: int):
        """Fetch and store all albums for an artist"""
        logger.info("Fetching artist discography...")
        
        albums = self.api.get_artist_albums(artist_id)
        
        if not albums:
            logger.warning("No albums found")
            return
        
        allowed_types = self.config.record_types()
        count = 0
        for album in albums:
            # Filter by record type
            if album.type not in allowed_types:
                logger.debug(f"Skipping {album.title} ({album.type}) - not in record_types")
                continue
            # Skip Various Artists compilations
            album_artist = getattr(album.artist, 'name', '') if album.artist else ''
            if album_artist.lower() in ('various artists', 'varios artistas', 'varios'):
                logger.debug(f"Skipping Various Artists album: {album.title} ({album.id})")
                continue

            if self.db.add_album(album, artist_id):
                count += 1
        
        logger.info(f"✓ Added {count} albums to database")
    
    def remove_by_name(self, artist_name: str) -> bool:
        """Remove artist from monitoring by name"""
        artist = self.db.get_artist_by_name(artist_name)
        
        if not artist:
            logger.error(f"Artist '{artist_name}' not found in monitoring list")
            return False
        
        return self.remove_by_id(artist['artist_id'])
    
    def remove_by_id(self, artist_id: int) -> bool:
        """Remove artist from monitoring by ID"""
        artist = self.db.get_artist(artist_id)
        
        if not artist:
            logger.error(f"Artist ID {artist_id} not found")
            return False
        
        if self.db.remove_artist(artist_id):
            logger.info(f"✓ Removed from monitoring: {artist['artist_name']}")
            return True
        
        return False
    
    def list_monitored(self):
        """List all monitored artists"""
        artists = self.db.get_all_artists()
        
        if not artists:
            print("\n📭 No artists being monitored.")
            print("💡 Use 'tidmon monitor add \"Artist Name\"' to add artists.\n")
            return
        
        print("\n" + "="*60)
        print("  👥 MONITORED ARTISTS")
        print("="*60 + "\n")
        
        for artist in artists:
            last_checked = artist['last_checked']
            if last_checked:
                last_checked_str = last_checked.split('T')[0]
            else:
                last_checked_str = "Never"
            print(f"  • {artist['artist_name']} (ID: {artist['artist_id']}, Last Checked: {last_checked_str})")
        print("\n")


    # --- Playlist Monitoring --- 

    def add_playlist(self, url: str):
        """Adds a playlist to the monitoring database."""
        parsed_url = url_utils.parse_url(url)
        if not parsed_url or parsed_url.tidal_type != url_utils.TidalType.PLAYLIST:
            print(f"  [!] Invalid TIDAL playlist URL: {url}")
            return

        playlist_uuid = parsed_url.tidal_id
        print(f"\n  🔍 Fetching playlist {playlist_uuid}...")

        try:
            playlist = self.api.get_playlist(playlist_uuid)
            if not playlist:
                print(f"  [!] Could not retrieve playlist with ID: {playlist_uuid}")
                return

            if self.db.add_playlist(playlist.uuid, playlist.title):
                print(f"  ✅ Playlist '{playlist.title}' added.")
                print(f"  📋 Fetching tracks...")
                tracks = self.api.get_playlist_items(playlist.uuid)
                if not tracks:
                    print(f"  ⚠️  Playlist '{playlist.title}' appears to be empty.")
                    return

                # Establish track baseline
                def get_id(item): return item.id if isinstance(item, BaseModel) else item.get('id')
                def get_artists(item): return item.artists if isinstance(item, BaseModel) else item.get('artists', [])

                track_ids = {get_id(track) for track in tracks if get_id(track) is not None}
                self.db.update_playlist_tracks(playlist.uuid, track_ids)
                print(f"  ✓  Baseline of {len(track_ids)} tracks established.")

                # Add all artists from the playlist to artist monitoring
                artist_ids_in_playlist = set()
                for track in tracks:
                    for artist in get_artists(track):
                        artist_id = get_id(artist)
                        if artist_id:
                            artist_ids_in_playlist.add(artist_id)

                total_artists = len(artist_ids_in_playlist)
                print(f"\n  👥 Found {total_artists} unique artists — adding to monitoring...\n")

                added_count = 0
                already_monitored_count = 0
                for artist_id in artist_ids_in_playlist:
                    artist_details = self.api.get_artist(artist_id)
                    if artist_details:
                        if self._add_artist(artist_details):
                            added_count += 1
                        else:
                            already_monitored_count += 1

                print(f"\n  ✅ Done — {added_count} new artist(s) added, {already_monitored_count} already monitored.")
                print(f"  💡 Run [tidmon refresh] to check for new releases.\n")
            else:
                print(f"  ⚠️  Playlist '{playlist.title}' is already being monitored.")

        except Exception as e:
            logger.error(f"Error adding playlist: {e}")
            print(f"  [!] Error: {e}")

    def add_playlists_from_file(self, filepath: str):
        """
        Add multiple playlists from a file.
        Each line should be a playlist URL.
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except FileNotFoundError:
            logger.error(f"File not found: {filepath}")
            return
        except Exception as e:
            logger.error(f"Error reading file: {e}")
            return

        logger.info(f"Importing playlists from {filepath}...")
        for line in lines:
            line = line.split('#')[0].strip()
            if not line:
                continue

            logger.info(f"Processing playlist URL: '{line}'")
            self.add_playlist(line)

    def remove_playlist(self, url: str):
        """Removes a playlist from monitoring."""
        parsed_url = url_utils.parse_url(url)
        if not parsed_url or parsed_url.tidal_type != url_utils.TidalType.PLAYLIST:
            logger.error("Invalid TIDAL playlist URL.")
            return
        
        playlist_uuid = parsed_url.tidal_id
        if self.db.remove_playlist(playlist_uuid):
            logger.info(f"✓ Playlist has been removed from monitoring.")
        else:
            logger.error(f"Could not remove playlist. Is it being monitored?")

    def list_playlists(self):
        """Lists all monitored playlists."""
        playlists = self.db.get_monitored_playlists()
        
        if not playlists:
            print("\n📭 No playlists being monitored.")
            print("💡 Use 'tidmon monitor playlist add <URL>' to add a playlist.\n")
            return
        
        print("\n" + "="*60)
        print("  🎶 MONITORED PLAYLISTS")
        print("="*60 + "\n")
        
        for playlist in playlists:
            last_checked = playlist['last_checked']
            if last_checked:
                last_checked_str = last_checked.split('T')[0]
            else:
                last_checked_str = "Never"
            print(f"  • {playlist['name']} (ID: {playlist['uuid']}, Last Checked: {last_checked_str})")
        print("\n")

    def clear_artists(self):
        """Removes all monitored artists."""
        if self.db.clear_artists():
            logger.info("All monitored artists have been removed.")
        else:
            logger.error("Could not remove monitored artists.")

    def clear_playlists(self):
        """Removes all monitored playlists."""
        if self.db.clear_playlists():
            logger.info("All monitored playlists have been removed.")
        else:
            logger.error("Could not remove monitored playlists.")

    def list_items(self, target: str):
        """List monitored artists and/or playlists."""
        if target in ('artists', 'all'):
            artists = self.db.get_all_artists()
            if artists:
                print("\nMonitored Artists:")
                for artist in artists:
                    print(f"  - ID: {artist['artist_id']}, Name: {artist['artist_name']}")
            else:
                print("\nNo artists are being monitored.")
        
        if target in ('playlists', 'all'):
            playlists = self.db.get_monitored_playlists()
            if playlists:
                print("\nMonitored Playlists:")
                for playlist in playlists:
                    print(f"  - UUID: {playlist['uuid']}, Name: {playlist['name']}")
            else:
                print("\nNo playlists are being monitored.")

    def export_to_file(self, file_path: str):
        """Export monitored artists and playlists to a file."""
        try:
            with open(file_path, 'w') as f:
                f.write("# Monitored Artists\n")
                artists = self.db.get_all_artists()
                if artists:
                    for artist in artists:
                        f.write(f"{artist['artist_id']}\n")
                else:
                    f.write("# No artists are being monitored.\n")

                f.write("\n# Monitored Playlists\n")
                playlists = self.db.get_monitored_playlists()
                if playlists:
                    for playlist in playlists:
                        f.write(f"https://tidal.com/browse/playlist/{playlist['uuid']}\n")
                else:
                    f.write("# No playlists are being monitored.\n")
            print(f"\nSuccessfully exported monitored lists to {file_path}")
        except Exception as e:
            print(f"\nError exporting to file: {e}")

    def remove_artist(self, identifier: str):
        """Remove a monitored artist by ID or name."""
        try:
            artist_id = int(identifier)
            artist = self.db.get_artist(artist_id)
            if not artist:
                print(f"No artist found with ID: {artist_id}")
                return
        except ValueError:
            # Identifier is a name
            artist = self.db.get_artist_by_name(identifier)
            if not artist:
                print(f"No artist found with name like: {identifier}")
                return
            artist_id = artist['artist_id']
        
        if self.db.remove_artist(artist_id):
            print(f"Successfully removed artist: {artist['artist_name']} (ID: {artist_id})")
        else:
            print(f"Could not remove artist with ID: {artist_id}")

    def export_albums(self, artist_identifier: Optional[str] = None, all_artists: bool = False, since: Optional[str] = None, until: Optional[str] = None, output_path: Optional[str] = None, include_downloaded: bool = False):
        """Exports a list of albums to a text file, formatted for tiddl."""
        artist_id = None
        artist_name = "all-artists"

        if artist_identifier:
            try:
                artist_id = int(artist_identifier)
                artist = self.db.get_artist(artist_id)
                if not artist:
                    logger.error(f'Artist ID "{artist_id}" not found in the monitoring list.')
                    return
                artist_name = artist['artist_name']
            except ValueError:
                artist = self.db.get_artist_by_name(artist_identifier)
                if not artist:
                    logger.error(f'Artist "{artist_identifier}" not found in the monitoring list.')
                    return
                artist_id = artist['artist_id']
                artist_name = artist['artist_name']
        
        elif not all_artists:
            logger.error("You must specify an artist or use the --all flag.")
            return

        export_type = "all" if include_downloaded else "pending"
        logger.info(f'Exporting {export_type} albums for "{artist_name}"...')

        albums = self.db.get_albums(artist_id=artist_id if not all_artists else None, since=since, until=until, include_downloaded=include_downloaded)

        if not albums:
            logger.info("No albums to export for the current selection.")
            return

        if not output_path:
            sanitized_name = "".join(c for c in artist_name if c.isalnum() or c in (' ', '_')).rstrip()
            output_path = f'{sanitized_name}_{export_type}_albums.txt'

        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                for album in albums:
                    album_url = f"https://tidal.com/album/{album['album_id']}"
                    f.write(f"tiddl download url {album_url}\n")
            
            logger.info(f"✓ Successfully exported {len(albums)} album(s) to \"{output_path}\"")
        except IOError as e:
            logger.error(f"Failed to write to file: {e}")