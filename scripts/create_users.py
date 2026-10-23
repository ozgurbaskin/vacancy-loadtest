#!/usr/bin/env python3
"""Create and verify the 3 test accounts; see ``vacancy_loadtest.provision`` for details.

uv run python scripts/create_users.py --email 'you+cyrex{n}@gmail.com' --count 3
"""

import sys

from vacancy_loadtest.provision import main

if __name__ == "__main__":
    sys.exit(main())
