import asyncio
import aiohttp
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict
from dataclasses import dataclass

from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.networking.api_helpers import auth_headers, utc_now


class AuthenticationError(Exception):
    """Raised when authentication fails."""
    pass


@dataclass
class AuthToken:
    """Container for authentication token and metadata."""

    token: str
    expires_at: datetime
    issued_at: datetime

    @property
    def is_expired(self) -> bool:
        """Check if token is expired or will expire within 5 minutes."""
        return utc_now() >= (self.expires_at - timedelta(minutes=5))

    @property
    def time_until_expiry(self) -> timedelta:
        """Time remaining until token expires."""
        return self.expires_at - utc_now()




class AuthManager:
    """Manages TopstepX API authentication with automatic token refresh."""

    def __init__(self, config: TopstepConfig):
        self.config = config
        self._token: Optional[AuthToken] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self.logger = logging.getLogger(__name__)

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()

    async def start(self):
        """Initialize the authentication manager."""
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
        )

        # Get initial token
        await self._authenticate()

        # Start refresh task
        self._refresh_task = asyncio.create_task(self._token_refresh_loop())
        self.logger.info("AuthManager started successfully")

    async def stop(self):
        """Shutdown the authentication manager."""
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

        if self._session:
            await self._session.close()

        self.logger.info("AuthManager stopped")

    def get_token(self) -> str:
        """Get current valid authentication token (synchronous)."""
        if not self._token:
            raise AuthenticationError(
                "No token available - ensure AuthManager.start() was called"
            )

        if self._token.is_expired:
            self.logger.warning("Token expired - background refresh should handle this")

        return self._token.token

    def access_token_factory(self) -> str:
        """Thread-safe synchronous accessor for SignalR client callbacks."""
        # This method is called by pysignalr from worker threads, so we need thread safety
        token = self._token
        return token.token if token else ""

    def get_auth_header(self) -> Dict[str, str]:
        """Get authentication header for HTTP requests."""
        token = self.get_token()
        return {"Authorization": f"Bearer {token}"}

    def validate_credentials(self, username: str, api_key: str) -> str:
        """Validate provided credentials and return the current token.

        Args:
            username: Username to validate.
            api_key: API key to validate.

        Returns:
            The current authentication token if credentials are valid.

        Raises:
            AuthenticationError: If the credentials do not match the configured ones
                or no token is available.
        """

        if username != self.config.username or api_key != self.config.api_key:
            raise AuthenticationError("Invalid credentials")

        return self.get_token()

    async def _authenticate(self):
        """Authenticate with TopstepX API and get JWT token."""
        if not self._session:
            raise AuthenticationError("AuthManager not started")

        auth_payload = {"userName": self.config.username, "apiKey": self.config.api_key}
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/plain",
        }

        try:
            async with self._session.post(
                f"{self.config.projectx_base_url}/api/Auth/loginKey", 
                json=auth_payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10, connect=3)
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise AuthenticationError(
                        f"Authentication failed: HTTP {response.status} - {error_text}"
                    )

                data = await response.json()

                if not data.get("success", False):
                    error_msg = data.get("errorMessage", "Unknown error")
                    raise AuthenticationError(f"Authentication failed: {error_msg}")

                token = data.get("token")
                if not token:
                    raise AuthenticationError("No token received from API")

                # Tokens expire in 24 hours according to docs
                issued_at = utc_now()
                expires_at = issued_at + timedelta(hours=24)

                self._token = AuthToken(
                    token=token, expires_at=expires_at, issued_at=issued_at
                )

                self.logger.info(
                    f"Authentication successful, token expires at {expires_at}"
                )

        except aiohttp.ClientError as e:
            raise AuthenticationError(f"Network error during authentication: {e}")
        except Exception as e:
            raise AuthenticationError(f"Unexpected error during authentication: {e}")

    async def _token_refresh_loop(self):
        """Background task to refresh token before it expires."""
        while True:
            try:
                if self._token:
                    # Refresh 1 hour before expiry
                    refresh_time = self._token.expires_at - timedelta(hours=1)
                    now = utc_now()

                    if now >= refresh_time:
                        self.logger.info("Refreshing authentication token")
                        # Try lightweight validation first, fall back to full auth on failure
                        try:
                            if await self.validate_token():
                                self.logger.debug(
                                    "Token refreshed via validation endpoint"
                                )
                            else:
                                self.logger.info(
                                    "Validation failed, performing full re-authentication"
                                )
                                await self._authenticate()
                        except Exception as e:
                            self.logger.warning(
                                f"Token validation failed: {e}, performing full re-authentication"
                            )
                            await self._authenticate()
                    else:
                        # Sleep until refresh time
                        sleep_seconds = (refresh_time - now).total_seconds()
                        await asyncio.sleep(
                            min(sleep_seconds, 3600)
                        )  # Check at least every hour
                else:
                    # No token yet, wait a bit
                    await asyncio.sleep(60)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in token refresh loop: {e}")
                # Retry after delay
                await asyncio.sleep(300)  # 5 minutes

    async def validate_token(self) -> bool:
        """Validate current token with the API and refresh if needed.

        If the API responds with a non-200 status, the response body is logged and
        ``False`` is returned.
        """
        if not self._token:
            return False

        try:
            headers = auth_headers(self._token.token)
            async with self._session.post(
                f"{self.config.projectx_base_url}/api/Auth/validate", headers=headers
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    self.logger.error(
                        f"Token validation failed: HTTP {response.status} - {body}"
                    )
                    return False

                data = await response.json()
                success = data.get("success", False)

                # Check for new token and update if provided
                new_token = data.get("newToken")
                if success and new_token:
                    self.logger.info(
                        "Received new token from validation, updating stored token"
                    )
                    # Update the stored token with new expiry time
                    issued_at = utc_now()
                    expires_at = issued_at + timedelta(hours=24)

                    self._token = AuthToken(
                        token=new_token, expires_at=expires_at, issued_at=issued_at
                    )

                return success

        except Exception as e:
            self.logger.error(f"Token validation failed: {e}")
            return False

    async def is_token_valid(self) -> bool:
        """Check if current token is valid (wrapper for validate_token)."""
        return await self.validate_token()
