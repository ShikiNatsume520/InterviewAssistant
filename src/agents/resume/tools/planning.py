"""Resume Agent计划控制工具；由图路由拦截，不进入普通 ToolNode。"""

from langchain_core.tools import tool


@tool
def request_plan() -> str:
    """当修改复杂或意图不确定时，请求制定计划并交由用户确认。

    本工具只发起计划流程，不代表计划已经批准，也不执行任何简历修改。
    系统会生成计划并通过独立确认节点与用户协同定稿；只有工具最终返回
    ``status=approved`` 后，才应直接按已批准计划执行，不得再次询问批准。
    """
    raise RuntimeError("request_plan 应由 Resume Agent工具路由拦截")
