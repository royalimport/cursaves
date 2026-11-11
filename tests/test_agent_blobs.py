"""Agent blob trees must survive export, push-status and repair intact.

Cursor stores agent state as agentKv:blob:{id} entries whose protobuf
payloads reference further blobs by 32-byte ID, sometimes inside nested
messages. Losing any blob in the tree breaks "continue chat" with
"Blob not found".
"""

import base64
import hashlib
import json
import sqlite3

import pytest

from cursor_saves import db, export, importer, paths

CID = "11111111-2222-3333-4444-555555555555"


def blob_id(name: str) -> bytes:
    return hashlib.sha256(name.encode()).digest()


def field(tag: int, payload: bytes) -> bytes:
    """Length-delimited protobuf field (single-byte tag and length)."""
    return bytes([tag, len(payload)]) + payload


ROOT, CHILD, GRANDCHILD, UNRELATED = (blob_id(n) for n in ("root", "child", "grandchild", "unrelated"))

# root -> nested message -> child -> grandchild; plus a 32-byte field that isn't a blob ID
BLOBS = {
    ROOT: field(0x0A, field(0x12, CHILD)) + field(0x1A, blob_id("not-a-blob")),
    CHILD: field(0x42, GRANDCHILD) + field(0x52, ROOT),
    GRANDCHILD: b'{"role":"assistant"}',
    UNRELATED: b"unrelated",
}


def conversation_state(*ids: bytes) -> str:
    return "~" + base64.b64encode(b"".join(field(0x0A, i) for i in ids)).decode()


def composer_data(messages: int = 2) -> dict:
    return {
        "composerId": CID,
        "conversationState": conversation_state(ROOT),
        "fullConversationHeadersOnly": [{"bubbleId": str(n)} for n in range(messages)],
    }


def make_db(path, blobs: dict[bytes, bytes], conv: dict) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
    conn.execute("CREATE TABLE cursorDiskKV (key TEXT UNIQUE, value BLOB)")
    conn.executemany(
        "INSERT INTO cursorDiskKV VALUES (?, ?)",
        [(f"agentKv:blob:{bid.hex()}", val) for bid, val in blobs.items()]
        + [(f"composerData:{CID}", json.dumps(conv))],
    )
    conn.commit()
    conn.close()


def stored_blob_ids(path) -> set[bytes]:
    with db.CursorDB(path) as cdb:
        return set(cdb.agent_blob_ids())


@pytest.fixture
def env(tmp_path, monkeypatch):
    global_db = tmp_path / "state.vscdb"
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    monkeypatch.setattr(paths, "get_global_db_path", lambda: global_db)
    monkeypatch.setattr(paths, "get_snapshots_dir", lambda: snapshots)
    monkeypatch.setattr(paths, "get_machine_id", lambda: "THIS-PC")
    return global_db, snapshots


def write_snapshot(snapshots, *, machine="THIS-PC", messages=2, blobs=None, tree=True):
    project = snapshots / "proj"
    project.mkdir(exist_ok=True)
    snapshot = {
        "version": 4,
        "composerId": CID,
        "composerData": composer_data(messages),
        "sourceMachine": machine,
        "projectIdentifier": "proj",
        "agentBlobs": {
            bid.hex(): base64.b64encode(val).decode() for bid, val in (blobs or {}).items()
        },
    }
    if tree:
        snapshot["agentBlobTree"] = True
    export.save_snapshot(snapshot, snapshots)


def test_export_includes_nested_blob_tree(env):
    global_db, _ = env
    make_db(global_db, BLOBS, composer_data())

    with db.CursorDB(global_db) as cdb:
        exported = export._extract_agent_blobs(composer_data(), cdb)

    assert set(exported) == {ROOT.hex(), CHILD.hex(), GRANDCHILD.hex()}
    assert base64.b64decode(exported[GRANDCHILD.hex()]) == BLOBS[GRANDCHILD]


def test_closure_reports_missing_blobs_without_looping():
    known = {ROOT, CHILD, GRANDCHILD}
    available = {ROOT: BLOBS[ROOT], CHILD: BLOBS[CHILD]}

    found, missing = export.collect_blob_closure(
        {ROOT.hex()}, lambda bid: available.get(bytes.fromhex(bid)), known
    )

    assert set(found) == {ROOT.hex(), CHILD.hex()}
    assert missing == {GRANDCHILD.hex()}


def test_saved_snapshot_meta_records_blob_tree(env):
    _, snapshots = env
    write_snapshot(snapshots, blobs={ROOT: BLOBS[ROOT]})

    meta = json.loads((snapshots / "proj" / f"{CID}.meta.json").read_text())
    assert meta["agentBlobTree"] is True


@pytest.mark.parametrize(
    ("machine", "tree", "expected"),
    [
        ("THIS-PC", False, "local_ahead"),  # own pre-fix snapshot gets re-pushed
        ("THIS-PC", True, "up_to_date"),
        ("OTHER-PC", False, "up_to_date"),  # never re-push another machine's chat
    ],
)
def test_push_status_reexports_own_pre_tree_snapshots(env, machine, tree, expected):
    global_db, snapshots = env
    make_db(global_db, BLOBS, composer_data())
    write_snapshot(snapshots, machine=machine, tree=tree)

    with db.CursorDB(global_db) as cdb:
        status = importer.get_push_status_for_conversation(CID, "proj", _cdb=cdb)

    assert status == expected


def test_push_status_behind_wins_over_old_format(env):
    global_db, snapshots = env
    make_db(global_db, BLOBS, composer_data(messages=2))
    write_snapshot(snapshots, messages=5, tree=False)

    with db.CursorDB(global_db) as cdb:
        assert importer.get_push_status_for_conversation(CID, "proj", _cdb=cdb) == "behind"


def test_repair_restores_nested_blob_from_snapshot(env):
    global_db, snapshots = env
    make_db(global_db, {ROOT: BLOBS[ROOT], CHILD: BLOBS[CHILD]}, composer_data())
    write_snapshot(snapshots, blobs={k: BLOBS[k] for k in (ROOT, CHILD, GRANDCHILD)})

    assert importer.repair_missing_blobs() == (1, 1)
    assert GRANDCHILD in stored_blob_ids(global_db)
    assert UNRELATED not in stored_blob_ids(global_db)


def test_repair_noop_when_tree_complete(env):
    global_db, snapshots = env
    make_db(global_db, BLOBS, composer_data())
    write_snapshot(snapshots, blobs={k: BLOBS[k] for k in (ROOT, CHILD, GRANDCHILD)})

    assert importer.repair_missing_blobs() == (0, 0)
