"""Fetch Commander tournament decklists for card2vec (AN-7, docs/CARD2VEC.md).

Source: the EDHTop16 public GraphQL API (https://edhtop16.com/api/graphql),
which serves TopDeck.gg tournament standings with each entry's maindeck.
EDHTop16's terms allow API use for developers; they ask that you don't
overload the service and don't imply endorsement. So this is sequential,
pages of PAGE tournaments, SLEEP seconds between requests, resumable
(cursor saved next to the output), and only run offline by hand. The app
never calls it.

Usage: python scripts/fetch_card2vec_data.py data/edhtop16.ndjson
       (env PAGE=10 SLEEP=3 MAX_PAGES=...; start elsewhere by writing an
       offset into data/edhtop16.ndjson.cursor)
Output: ndjson lines {tid, date, size, standing, commander, cards:[...]};
entries with fewer than 50 maindeck cards (no list submitted) are skipped.
"""
import json, os, sys, time, urllib.request

API = "https://edhtop16.com/api/graphql"
OUT = sys.argv[1] if len(sys.argv) > 1 else "edhtop16.ndjson"
STATE = OUT + ".cursor"
PAGE = int(os.environ.get("PAGE", "10"))
SLEEP = float(os.environ.get("SLEEP", "3"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "100000"))
Q = """query($after:String,$first:Int!){ tournaments(first:$first, after:$after, sortBy:DATE,
 filters:{timePeriod:ALL_TIME}) { edges { node { TID size tournamentDate
 entries { standing commander { name } maindeck { name } } } }
 pageInfo { hasNextPage endCursor } } }"""


def post(after):
    body = json.dumps({"query": Q, "variables": {"after": after, "first": PAGE}}).encode()
    req = urllib.request.Request(API, body, {"Content-Type": "application/json",
                                             "User-Agent": "commander-lab card2vec trainer (personal, noncommercial)"})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.load(r)


after = open(STATE).read().strip() if os.path.exists(STATE) else None
after = after or None
pages = decks = 0
with open(OUT, "a") as out:
    while pages < MAX_PAGES:
        for attempt in range(5):
            try:
                d = post(after)
                break
            except Exception as e:  # noqa
                print("retry", attempt, repr(e), flush=True)
                time.sleep(20 * (attempt + 1))
        else:
            sys.exit("giving up at cursor %s" % after)
        t = d["data"]["tournaments"]
        for edge in t["edges"]:
            n = edge["node"]
            for en in n["entries"]:
                cards = [c["name"] for c in (en.get("maindeck") or [])]
                if len(cards) < 50:
                    continue
                out.write(json.dumps({"tid": n["TID"], "date": n["tournamentDate"], "size": n["size"],
                                      "standing": en.get("standing"),
                                      "commander": (en.get("commander") or {}).get("name"),
                                      "cards": cards}) + "\n")
                decks += 1
        out.flush()
        after = t["pageInfo"]["endCursor"]
        open(STATE, "w").write(after or "")
        pages += 1
        if pages % 20 == 0:
            print(f"pages={pages} cursor={after} decks={decks} last={t['edges'][-1]['node']['tournamentDate'] if t['edges'] else None}", flush=True)
        if not t["pageInfo"]["hasNextPage"]:
            print("done", flush=True)
            break
        time.sleep(SLEEP)
print("total decks this run", decks)
