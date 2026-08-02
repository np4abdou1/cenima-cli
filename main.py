#!/usr/bin/env python3
"""
cenima-cli - Main entry point
Browse and stream movies, series and anime in FHD in Arabic!
"""
import sys
from pathlib import Path

# Ensure local package directory is on sys.path so main.py works standalone without build
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from cenima.cli import main
except ImportError as e:
    print(f"Error: Failed to import cenima package: {e}")
    sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
