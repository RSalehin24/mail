import dataclasses
import sqlite3

from helper.forwarding import ForwardingControl


def configure_forwarding(fixture):
    root = fixture.host.root
    data = root / "forwarding-mail-data"
    data.mkdir(mode=0o700, exist_ok=True)
    with sqlite3.connect(data / "imapsql.db") as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT);
            CREATE TABLE IF NOT EXISTS extKeys(id TEXT,uid INTEGER);
            CREATE TABLE IF NOT EXISTS mboxes(id INTEGER PRIMARY KEY,uid INTEGER,name TEXT);
            CREATE TABLE IF NOT EXISTS msgs(
                extBodyKey TEXT,mboxId INTEGER,date REAL,compressAlgo TEXT);
        """)
        for i, email in enumerate(sorted(fixture.host.maddy.storage), 1):
            db.execute("INSERT OR IGNORE INTO users VALUES(?,?)", (i, email))
    control = ForwardingControl(root, data)
    fixture.host.helper.dependencies = dataclasses.replace(
        fixture.host.helper.dependencies, forwarding=control
    )
    return control
