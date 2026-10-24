"""First-admin bootstrap: `python -m wishlist.bootstrap`."""

from __future__ import annotations

import os
import sys

from sqlalchemy import func, select

from . import create_app, members
from .config import Config, ConfigError
from .errors import ValidationError
from .db import get_session, upgrade_db
from .keys import personal_link
from .models import User
from .notify import get_mailer


def main(*, mailer=None) -> int:
    try:
        config = Config.from_env(os.environ)
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 1
    upgrade_db(config.database_url)
    app = create_app(config, mailer=mailer)
    with app.app_context():
        s = get_session()
        if s.scalar(select(func.count()).select_from(User)):
            return 0
        if not config.admin_email:
            print(
                "Warning: no members exist and ADMIN_EMAIL is not set; "
                "set it and restart to create the first admin.",
                file=sys.stderr,
            )
            return 0
        name = config.admin_name or config.admin_email.split("@")[0]
        try:
            user, key = members.create_member(
                s, name=name, email=config.admin_email, language=config.default_language, is_admin=True
            )
        except ValidationError as e:
            print(f"Cannot create the first admin: {e.message}", file=sys.stderr)
            return 1
        print(f"Admin link: {personal_link(config.base_url, key)}", flush=True)
        members.send_link(user, key, get_mailer())
    return 0


if __name__ == "__main__":
    sys.exit(main())
