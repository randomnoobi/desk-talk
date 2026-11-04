"""The admin account: made from ADMIN_USERNAME and ADMIN_PASSWORD on start, kept with a hashed password (argon2, with
the library's own settings), and the login check the admin routes use. With neither set there is no admin login."""

from __future__ import annotations

import logging

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request

from desktalk_cloud.config import CloudConfig
from desktalk_cloud.deps import parts_of
from desktalk_cloud.domain import Admin, is_clean_text
from desktalk_cloud.ports import Store

__all__ = ['SESSION_ADMIN', 'admin_login_ok', 'admin_set_up', 'current_admin', 'ensure_admin', 'hash_password',
           'verify_password']

SESSION_ADMIN = 'admin_username'            # the admin's login cookie holds this, apart from a person's 'user_id'

log = logging.getLogger(__name__)
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


_HASH_NOBODY_HAS = hash_password('no one has this password')      # made once, up front, so no login pays for it


def admin_set_up(config: CloudConfig) -> bool:
    """True when both ADMIN_USERNAME and ADMIN_PASSWORD are set."""
    secrets = config.secrets
    return bool(secrets.admin_username) and secrets.admin_password is not None


def _the_admin(config: CloudConfig, store: Store, username: object) -> Admin | None:
    """The saved admin, when the settings still name this username (an old admin whose name was taken out of .env
    can't log in)."""
    if not admin_set_up(config) or not isinstance(username, str) or username != config.secrets.admin_username:
        return None
    return store.get_admin(username)


def admin_login_ok(config: CloudConfig, store: Store, username: str, password: str) -> bool:
    """True for the right username and password. Takes as long for a wrong username as for a wrong password."""
    admin = _the_admin(config, store, username)
    if admin is None:
        verify_password(_HASH_NOBODY_HAS, password)
        return False
    return verify_password(admin.password_hash, password)


def ensure_admin(config: CloudConfig, store: Store) -> bool:
    """Make the admin from the settings on first start. True when it made one. An admin that is already there is left
    alone (a changed password in .env does not change it: the log says so)."""
    secrets = config.secrets
    username, password = secrets.admin_username, secrets.admin_password
    if username is None and password is None:
        return False
    if username is None or password is None:
        log.warning('Only one of ADMIN_USERNAME and ADMIN_PASSWORD is set, so there is no admin login. '
                    'Set both in cloud/.env.')
        return False
    if not is_clean_text(username):
        log.warning('ADMIN_USERNAME has a character the server can not keep, so there is no admin login.')
        return False
    made = store.create_admin_if_missing(username, hash_password(password.get_secret_value()))
    if not made:
        saved = store.get_admin(username)
        if saved is not None and not verify_password(saved.password_hash, password.get_secret_value()):
            log.warning('ADMIN_PASSWORD in cloud/.env is not the password saved for the admin, so the saved one '
                        'still works. Changing it here does not change it.')
    return made


def current_admin(request: Request) -> Admin:
    """The admin in the login cookie; 401 for anyone else (a person logged in with Google included)."""
    parts = parts_of(request)
    admin = _the_admin(parts.config, parts.store, request.session.get(SESSION_ADMIN))
    if admin is None:
        raise HTTPException(401, parts.config.messages.not_logged_in)
    return admin
