"""路由测试自行组装 FastAPI() 时复刻生产注册处的认证挂法。

``server/app.py`` 给受保护 router 挂 ``dependencies=[Depends(get_current_user)]``，
端点签名里不再声明 ``CurrentUser``。mini app 少挂这一层，测到的就是一个无认证的
应用——多数用例用 ``dependency_overrides`` 绕过认证，因而不会失败，只有断言 401
的用例会暴露。
"""

from fastapi import Depends, FastAPI

from server.auth import CurrentUserInfo, get_current_user

AUTH_DEPENDENCIES = [Depends(get_current_user)]


def override_auth(app: FastAPI) -> None:
    """放行 mini app 的认证，等价于测试用户已登录。

    给不关心认证、只测业务行为的用例用；断言 401 的用例不要调它。
    """
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
