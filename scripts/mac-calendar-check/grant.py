"""Let these programs use the calendars, as clicking Allow would, on a CI
Mac (GitHub's have SIP off, so TCC's databases can be written). Run with sudo.

    sudo /usr/bin/python3 grant.py /path/to/program bundle:org.biomanager.desktop …

Only for a throwaway machine: it writes macOS's privacy settings directly.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

DATABASES = ("/Library/Application Support/com.apple.TCC/TCC.db",
             os.path.expanduser(f"~{os.environ.get('SUDO_USER') or 'runner'}/Library/Application Support/com.apple.TCC/TCC.db"))


def main(clients: list[str]) -> None:
    now = int(time.time())
    for db in DATABASES:
        if not os.path.exists(db):
            print("no", db)
            continue
        con = sqlite3.connect(db)
        columns = [row[1] for row in con.execute("PRAGMA table_info(access)")]
        for client in clients:
            kind, ident = (0, client[len("bundle:"):]) if client.startswith("bundle:") else (1, client)
            row = {"service": "kTCCServiceCalendar", "client": ident, "client_type": kind, "auth_value": 2,
                   "auth_reason": 4, "auth_version": 1, "csreq": None, "policy_id": None,
                   "indirect_object_identifier_type": 0, "indirect_object_identifier": "UNUSED",
                   "indirect_object_code_identity": None, "flags": 0, "last_modified": now, "pid": None,
                   "pid_version": None, "boot_uuid": "UNUSED", "last_reminded": now, "prompt_count": 0}
            use = [c for c in columns if c in row]
            con.execute(f"INSERT OR REPLACE INTO access ({','.join(use)}) VALUES ({','.join('?' * len(use))})",
                        [row[c] for c in use])
        con.commit()
        print(db, "->", [c for c, in con.execute("SELECT client FROM access WHERE service='kTCCServiceCalendar'")])


if __name__ == "__main__":
    main(sys.argv[1:])
