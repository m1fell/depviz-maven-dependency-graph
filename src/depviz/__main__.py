"""Start depviz from the module command or the IDE launch configuration."""

if __package__:
    from .cli import main
else:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from depviz.cli import main

raise SystemExit(main())
