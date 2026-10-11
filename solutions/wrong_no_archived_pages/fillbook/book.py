"""Combine every stored export into one view of the account."""

import json
from typing import Dict, List, Set

from fillbook.records import ExportError, Fill, Settlement, from_rest, from_v1, settlement
from fillbook.store import Export


class Book:
    def __init__(self, exports: List[Export]):
        self.exports = exports
        self.fills: Dict[str, Fill] = {}
        self.busted: Set[str] = set()
        self.settlements: Dict[str, Settlement] = {}   # latest revision per market
        self.statements: List[dict] = []
        for exp in exports:
            self._read(exp)
        for fid in self.busted:
            self.fills.pop(fid, None)

    def _add_fill(self, f: Fill) -> None:
        prev = self.fills.get(f.fill_id)
        if prev is None:
            self.fills[f.fill_id] = f
        elif prev._replace(ts=f.ts) != f or abs((prev.ts - f.ts).total_seconds()) >= 1:
            raise ExportError("fill %s differs between records: %r vs %r" % (f.fill_id, prev, f))

    def _read(self, exp: Export) -> None:
        seen = set()
        cursor = "index"
        while cursor is not None:
            if cursor in seen:
                raise ExportError("%s: cursor loop at page %s" % (exp.export_id, cursor))
            seen.add(cursor)
            if cursor not in exp.pages:
                raise ExportError("%s: missing page %s" % (exp.export_id, cursor))
            page = json.loads(exp.pages[cursor])
            version = page.get("api_version", 2)
            if version not in (1, 2):
                raise ExportError("%s: page %s has api_version %r" % (exp.export_id, cursor, version))
            for r in page["fills"]:
                f = from_rest(r)
                if r.get("status", "executed") == "busted":
                    self.busted.add(f.fill_id)
                self._add_fill(f)
            cursor = page.get("cursor")
        for _, line in sorted(exp.ws.items()):
            msg = json.loads(line)
            if msg.get("type") == "fill":
                self._add_fill(from_v1(msg["msg"]))
            elif msg.get("type") == "fill_bust":
                self.busted.add(msg["msg"]["fill_id"])
        for r in json.loads(exp.settlements)["settlements"]:
            s = settlement(r)
            prev = self.settlements.get(s.ticker)
            if prev is None or s.revision > prev.revision:
                self.settlements[s.ticker] = s
        acct = json.loads(exp.account)
        if acct:
            self.statements.append(dict(acct, export_id=exp.export_id))

    def live_fills(self) -> List[Fill]:
        return sorted(self.fills.values(), key=lambda f: (f.ts, f.fill_id))
