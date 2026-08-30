"""端点测试的失败信号。

定义不合法与渲染期错误在接口上是同一件事——「这份定义现在跑不了」——所以两者共用
:class:`DefinitionDiagnostics`：校验器产出的诊断原样带出，渲染期错误包成同一形状的一条诊断。
消费方（UI 高亮、Agent 定位、测试断言）因此只需要认一套 ``{path, code, message}``。
"""

from __future__ import annotations

from lib.custom_provider.endpoint_definition import (
    DefinitionDiagnostics,
    DefinitionErrorCode,
    DefinitionIssue,
    TemplateRenderError,
)


class EndpointTestDefinitionError(Exception):
    """这份定义无法执行所请求的测试，携带与保存接口同构的诊断。"""

    def __init__(self, diagnostics: DefinitionDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__("endpoint definition cannot be executed")

    @classmethod
    def from_render_failure(cls, path: str, detail: str | TemplateRenderError) -> EndpointTestDefinitionError:
        """把一次模板渲染失败（占位符缺值、混合文本遇 null、enum 缺项）包成单条诊断。"""
        if isinstance(detail, TemplateRenderError):
            issue = DefinitionIssue(path=path, code=detail.code, params=detail.message.params)
        else:
            issue = DefinitionIssue(
                path=path,
                code=DefinitionErrorCode.TEMPLATE_RENDER_FAILED,
                params={"detail": detail},
            )
        return cls(DefinitionDiagnostics(errors=(issue,)))
