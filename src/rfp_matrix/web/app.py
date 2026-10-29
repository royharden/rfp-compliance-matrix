"""Optional dependency boundary. Factory builds source-defined routes, never serves."""
from pathlib import Path

from .security import LocalBoundaryMiddleware, LocalSessionAuthority, WebError


def create_app(application, *, port: int, authority: LocalSessionAuthority | None = None):
    if type(port) is not int or not 20000 <= port <= 29999:
        raise WebError("claimed_port_required", 422)
    try:
        from fastapi import FastAPI
        from jinja2 import Environment, FileSystemLoader, StrictUndefined
    except ImportError as exc:
        raise WebError("optional_web_dependencies_unavailable", 503) from exc
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, debug=False)
    templates = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
                            autoescape=True, undefined=StrictUndefined)
    app.state.reviewer_application = application
    app.state.session_authority = authority or LocalSessionAuthority()
    app.add_middleware(LocalBoundaryMiddleware, port=port)
    from .routes import install_routes
    install_routes(app, application=application, authority=app.state.session_authority, templates=templates)
    return app
