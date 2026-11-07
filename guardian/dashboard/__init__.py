"""Web dashboard (laptop and phone), Malayalam and English.

Security model:
  * Binds to 127.0.0.1 by default. Any other address needs --allow-remote;
    the recommended remote path is Tailscale (private, encrypted network).
  * Separate dashboard password (scrypt hash), login rate limiting.
  * Server-side sessions, HttpOnly + SameSite=Strict cookie, CSRF token on
    every state-changing request, strict security headers.
  * Credential values are write-only: never rendered back.
"""
