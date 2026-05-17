#!/usr/bin/env python
"""Retire N matcher-created canonical_event_links so the matcher re-processes them on its next poll cycle.

Use during a canonical_event_matcher dev soak to simulate new unlinked
platform_events arriving (without having to seed real platform_events +
games). Each retirement makes one pe_id appear as "unlinked" to
_select_candidates, so the matcher's next poll will recreate the link.

Origin: session 111 dev soak (Option 3 — exercise active-work path).
Safe: only retires links with link_state='active'; never deletes data.

Usage:
    PRECOG_ENV=dev python scripts/soak_inject_unlinked.py [N]

where N defaults to 3.
"""

import sys

from precog.database.connection import get_cursor


def main(n: int = 3) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE canonical_event_links
            SET link_state = 'retired',
                retired_at = NOW(),
                retire_reason = 'soak-test-injection-session-111'
            WHERE id IN (
                SELECT id FROM canonical_event_links
                WHERE link_state = 'active'
                ORDER BY id DESC
                LIMIT %s
            )
            RETURNING id, platform_event_id, canonical_event_id
            """,
            (n,),
        )
        rows = cur.fetchall()
        if not rows:
            print("No active links retired (none matched).")
            return

        # Matcher selector also fences on games.canonical_event_id IS NULL
        # (see canonical_event_matcher._select_candidates only_unlinked_games
        # branch). Retiring the link alone is NOT enough; we also have to
        # reset the games back-pointer for matcher pickup.
        pe_ids = [r["platform_event_id"] for r in rows]
        cur.execute(
            """
            UPDATE games SET canonical_event_id = NULL
            WHERE id IN (
                SELECT game_id FROM platform_events WHERE id = ANY(%s)
            )
            RETURNING id
            """,
            (pe_ids,),
        )
        game_rows = cur.fetchall()

        print(f"Retired {len(rows)} active link(s):")
        for r in rows:
            print(
                f"  link_id={r['id']:>6}  pe_id={r['platform_event_id']:>6}  "
                f"was-canonical_event_id={r['canonical_event_id']:>6}"
            )
        print(f"Reset {len(game_rows)} games.canonical_event_id back-pointer(s) to NULL.")
        print()
        print(
            "These pe_ids will appear as 'unlinked' to the matcher's next "
            "_select_candidates query and should be re-matched within "
            "poll_interval_sec (default 30s)."
        )


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    main(n)
