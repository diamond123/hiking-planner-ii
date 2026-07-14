"""One-off manual script: render the compiled LangGraph agent as a PNG diagram.

Importing app.graph opens the module-level Qdrant singleton (see app/qdrant_store.py),
so run this with the server NOT running (same lock caveat as verify_qdrant.py).

    uv run python scripts/draw_graph.py [output_path]

Renders via the hosted mermaid.ink API (default draw_method), so it needs network
access. Defaults to writing backend/graph.png if no path is given.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.graph import compiled_graph


def main():
    output_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "graph.png"
    )
    compiled_graph.get_graph(xray=True).draw_mermaid_png(output_file_path=output_path)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
