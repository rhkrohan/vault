---
date: 2026-09-12
chat_id: synth-002
---

## User

Back on acme-platform. We need to decide on authentication. Options on the
table are hand-rolled server-side sessions, JWTs, or an auth provider like
Clerk. Given it's still just me and the search team helping out part time
for now, what's the simplest path?

## Assistant

If it's mostly internal users and you already have a Postgres-backed user
table from the legacy Rails app, hand-rolled sessions are the least new
surface area: no third-party dependency, and you already understand the
session cookie model from Rails. JWTs add complexity you don't need yet
(revocation, rotation), and Clerk is more useful once you need SSO or
external users.

## User

That makes sense, let's go with sessions for now. Decision: auth = sessions
for acme-platform, revisit if we ever need SSO. I'm still formally on the
search team for headcount purposes even though I'm spending all my time on
acme-platform this quarter.

## Assistant

Sounds like a reasonable default. I'll assume server-side sessions backed
by Postgres for acme-platform going forward. Let me know if the SSO
requirement comes up later and we can revisit Clerk or another provider.

## User

Will do. One more thing: Jordan mentioned in standup that leadership wants
a cost estimate for the quarter, but that's her problem, not mine yet.
