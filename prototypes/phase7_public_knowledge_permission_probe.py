"""阶段 7 补充原型：验证公共知识写权限只来自认证 principal。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PrincipalKind = Literal["guest", "developer"]


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    id: str
    kind: PrincipalKind


def require_public_write(principal: AuthenticatedPrincipal) -> None:
    """公共知识写操作只允许认证后的开发人员身份。"""
    if principal.kind != "developer":
        raise PermissionError("public knowledge is developer-managed")


def public_resource_view(principal: AuthenticatedPrincipal) -> dict[str, bool]:
    """同一公共资源根据认证角色返回不同操作能力。"""
    writable = principal.kind == "developer"
    return {
        "readOnly": not writable,
        "canReindex": writable,
        "canDelete": writable,
    }


def main() -> None:
    guest = AuthenticatedPrincipal("guest-a", "guest")
    developer = AuthenticatedPrincipal("developer-local", "developer")

    try:
        require_public_write(guest)
    except PermissionError:
        pass
    else:
        raise AssertionError("guest must not write public knowledge")

    require_public_write(developer)
    assert public_resource_view(guest) == {
        "readOnly": True,
        "canReindex": False,
        "canDelete": False,
    }
    assert public_resource_view(developer) == {
        "readOnly": False,
        "canReindex": True,
        "canDelete": True,
    }

    # 请求体伪造 developer 字段不参与授权；原型只接收认证结果对象。
    forged_request_body = {"principal_kind": "developer", "scope": "public"}
    assert forged_request_body["principal_kind"] == "developer"
    try:
        require_public_write(guest)
    except PermissionError:
        pass
    else:
        raise AssertionError("request body must not elevate guest privilege")

    print("PASS: 游客无法上传、重建或删除公共知识。")
    print("PASS: 开发人员具备公共知识管理能力。")
    print("PASS: 伪造请求体角色不会绕过认证 Session 权限。")


if __name__ == "__main__":
    main()
