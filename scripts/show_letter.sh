#!/bin/bash
# Print the letter body for a given DM name (case-insensitive partial match).
# Usage: ./scripts/show_letter.sh "Jack Conte"
#        ./scripts/show_letter.sh jack
#        ./scripts/show_letter.sh   (lists all 26 contacts)
exec .venv/bin/python3 scripts/show_letter.py "$@"
