"""Endpoint-test failure signals.

Invalid definitions/render failures mean the same thing at APIs: this definition cannot run now. Share
:class:`DefinitionDiagnostics`, passing validator diagnostics unchanged and wrapping rendering failures into one equivalent item.
UI highlighting/Agent location/test assertions therefore recognize one ``{path, code, message}`` shape.
"""

from __future__ import annotations

from arcreel_market_core.endpoint_definition import (
    DefinitionDiagnostics,
    DefinitionErrorCode,
    DefinitionIssue,
    JsonPathEvaluationError,
    TemplateRenderError,
)


class EndpointTestDefinitionError(Exception):
    """Definitions unable to execute requested tests, with save-interface-compatible diagnostics."""

    def __init__(self, diagnostics: DefinitionDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__("endpoint definition cannot be executed")

    @classmethod
    def from_render_failure(
        cls, path: str, detail: str | TemplateRenderError | JsonPathEvaluationError
    ) -> EndpointTestDefinitionError:
        """Wrap template-rendering/JSONPath-evaluation failures into single structured diagnostics."""
        if isinstance(detail, TemplateRenderError | JsonPathEvaluationError):
            issue = DefinitionIssue(path=path, code=detail.code, params=detail.message.params)
        else:
            issue = DefinitionIssue(
                path=path,
                code=DefinitionErrorCode.TEMPLATE_RENDER_FAILED,
                params={"detail": detail},
            )
        return cls(DefinitionDiagnostics(errors=(issue,)))
