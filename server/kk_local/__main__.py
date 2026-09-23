"""Unified application entry; legacy offline imports remain compatible."""
from .app.cli import main
from .app.configuration import account_endpoints,match_point_policy
from .app.offline import run


if __name__=='__main__':main()
