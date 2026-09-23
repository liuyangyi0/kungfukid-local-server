"""Compatibility entry for the explicit host-only lab; no authentication bypass by default."""
from .app.lab import run,lab_endpoints


def main(argv=None):
    from .app.cli import run_mode
    run_mode('lab',argv)


if __name__=='__main__':main()
