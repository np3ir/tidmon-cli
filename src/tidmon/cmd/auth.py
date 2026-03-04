import logging
import webbrowser
from datetime import timedelta
from time import time, sleep

from rich.console import Console

from tidmon.core.auth_client import AuthAPI, load_auth_data, save_auth_data
from tidmon.core.auth_exceptions import AuthClientError
from tidmon.core.auth_models import AuthData

logger = logging.getLogger(__name__)
console = Console()


def _format_remaining(expires_at: int) -> str:
    """Format remaining token lifetime as 'Xd Xh Xm'."""
    seconds = max(0, expires_at - int(time()))
    remaining = timedelta(seconds=seconds)
    days = remaining.days
    hours, rem = divmod(remaining.seconds, 3600)
    mins, _ = divmod(rem, 60)
    return f"{days}d {hours}h {mins}m"


class Auth:
    """Handles authentication using tiddl's AuthAPI + AuthData pattern."""

    def __init__(self):
        self.auth_api = AuthAPI()

    def login(self):
        """Initiates the device authentication flow."""
        loaded = load_auth_data()

        # Skip login if a valid (non-expired) token already exists
        if loaded.token and loaded.expires_at and loaded.expires_at > int(time()):
            console.print("[cyan bold]Already logged in.")
            return

        try:
            device_auth = self.auth_api.get_device_auth()
        except Exception as e:
            logger.error(f"Failed to start device auth: {e}", exc_info=True)
            console.print(f"[bold red]Error starting authentication: {e}")
            return

        uri = device_auth.verificationUriComplete
        if not uri.startswith("http"):
            uri = f"https://{uri}"
        try:
            webbrowser.open(uri)
        except Exception:
            pass

        console.print(f"\nGo to '[link]{uri}[/link]' and complete authentication!")

        auth_end_at = time() + device_auth.expiresIn
        status_text = "Authenticating..."

        with console.status(status_text) as status:
            while True:
                # Hard time-based fail-safe independent of server error semantics
                if time() >= auth_end_at:
                    status.console.print(
                        "[bold red]Authentication timed out. Please try again."
                    )
                    break

                sleep(device_auth.interval)

                try:
                    auth = self.auth_api.get_auth(device_auth.deviceCode)
                    auth_data = AuthData(
                        token=auth.access_token,
                        refresh_token=auth.refresh_token,
                        expires_at=auth.expires_in + int(time()),
                        user_id=str(auth.user_id),
                        country_code=auth.user.countryCode,
                    )
                    save_auth_data(auth_data)
                    status.console.print("[bold green]Logged in!")
                    break

                except AuthClientError as e:
                    if e.error == "authorization_pending":
                        time_left = auth_end_at - time()
                        minutes, seconds = time_left // 60, int(time_left % 60)
                        status.update(
                            f"{status_text} time left: {minutes:.0f}:{seconds:02d}"
                        )
                        continue

                    if e.error == "expired_token":
                        status.console.print(
                            "[bold red]Authentication time expired."
                        )
                        break

                    logger.error(f"Auth error: {e}", exc_info=True)
                    status.console.print(f"[bold red]Authentication error: {e}")
                    break

    def logout(self):
        """Logs out and clears the saved token."""
        loaded = load_auth_data()

        if loaded.token:
            try:
                self.auth_api.logout_token(loaded.token)
            except Exception as e:
                logger.warning(f"Logout request failed: {e}")

        save_auth_data(AuthData())
        console.print("[bold green]Logged out!")

    def status(self):
        """Displays the current authentication status."""
        loaded = load_auth_data()
        console.print("\n--- AUTHENTICATION STATUS ---")

        if not loaded.token:
            console.print("Status:  Not authenticated")
            console.print("\nRun 'tidmon auth' to log in.")
            return

        console.print("Status:    Authenticated [green]✓[/green]")
        console.print(f"User ID:   {loaded.user_id}")
        console.print(f"Country:   {loaded.country_code}")

        if loaded.expires_at:
            if loaded.expires_at <= int(time()):
                console.print(
                    "Token:     [bold red]EXPIRED[/bold red]. Run 'tidmon auth' to log in again."
                )
            else:
                console.print(f"Token:     Expires in {_format_remaining(loaded.expires_at)}")

        console.print("-----------------------------")

    def refresh(self, force: bool = False, early_expire: int = 0):
        """Refreshes the access token using the saved refresh token."""
        loaded = load_auth_data()

        if not loaded.refresh_token:
            console.print("[bold red]Not logged in.")
            return

        safe_early_expire = max(0, early_expire)
        if not force and loaded.expires_at and time() < (loaded.expires_at - safe_early_expire):
            console.print(
                f"[green]Auth token expires in {_format_remaining(loaded.expires_at)}"
            )
            return

        try:
            auth = self.auth_api.refresh_token(loaded.refresh_token)
        except Exception as e:
            logger.error(f"Token refresh failed: {e}", exc_info=True)
            console.print(f"[bold red]Failed to refresh token: {e}")
            return

        loaded.token = auth.access_token
        loaded.expires_at = auth.expires_in + int(time())
        if auth.refresh_token:
            loaded.refresh_token = auth.refresh_token

        save_auth_data(loaded)
        console.print("[bold green]Auth token has been refreshed!")
        console.print(f"[green]Expires in {_format_remaining(loaded.expires_at)}")
