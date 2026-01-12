#!/usr/bin/env python3
"""
cenima-cli - Main entry point
Browse and stream movies, series and anime in FHD in Arabic!
"""
import sys

try:
    from cenima.cli import main
except ImportError as e:
    print(f"Error: Failed to import cenima package. {e}")
    print("Make sure you have installed the package correctly:")
    print("  pip install -e .")
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
