"""Optional local reviewer. Importing this package never starts a server."""


def create_app(application, *, port, authority=None):
    from .app import create_app as factory
    return factory(application, port=port, authority=authority)
