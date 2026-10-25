"""HAYAT's local web access -- reach the same brain from a phone or any device.

The server here reuses the exact runtime the desktop window uses: one brain, one
memory, whether the request comes from the keyboard or a browser on the private
mesh. Every remote turn runs under the REMOTE_OWNER principal, so the authority
kernel gives it read and reversible-local work but keeps delete, shell, install,
and everything outward-facing for the console. Nothing is ever exposed to the
public internet by this module; it binds to localhost by default and refuses to
start without an access token.
"""
