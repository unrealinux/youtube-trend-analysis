"""Point the whole test session at a throwaway database.

`app.database.DATABASE_PATH` defaults to a relative `youtube_history.db`, so
tests that exercise /api/history used to mutate the developer's real database
(test_history_endpoints deletes every row). Import-time redirection is the only
reliable hook: the path is read when app.database is first imported, which
happens partway through collection.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["YT_HISTORY_DB"] = os.path.join(tempfile.mkdtemp(prefix="yt-tests-"), "test.db")

import app.database as _database

_database.DATABASE_PATH = os.environ["YT_HISTORY_DB"]
