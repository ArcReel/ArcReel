"""Reproduce production authentication wiring when router tests assemble FastAPI() themselves.

``server/app.py`` attaches ``dependencies=[Depends(get_current_user)]`` to protected routers;
endpoint signatures no longer declare ``CurrentUser``. Omitting this layer from a mini app tests an
unauthenticated application. Most cases bypass authentication through ``dependency_overrides``
and therefore pass; only cases asserting 401 expose the omission.
"""

from fastapi import Depends, FastAPI

from server.auth import CurrentUserInfo, get_current_user

AUTH_DEPENDENCIES = [Depends(get_current_user)]


def override_auth(app: FastAPI) -> None:
    """Allow mini-app authentication, equivalent to the test user being logged in.

    For cases testing business behavior without caring about authentication; do not call in cases asserting 401.
    """
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
