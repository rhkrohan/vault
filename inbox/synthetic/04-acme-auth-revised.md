---
date: 2026-09-18
chat_id: synth-004
---

## User

Update on acme-platform: it's official now, I transferred off the search
team onto the infra team, and acme-platform is my full-time project under
infra. Also, the auth decision changed. Leadership wants acme-platform to
support single sign-on so contractors from a partner org can log in with
their own identity provider. Hand-rolled sessions won't get us there
without a lot of extra work.

## Assistant

That changes the calculus. For SSO with an external identity provider,
Clerk (or a similar hosted auth provider) handles the SAML/OIDC integration
for you instead of you building it. Given you already decided against
adding third-party complexity before, this is exactly the kind of
requirement that justifies revisiting that.

## User

Agreed. Let's evaluate JWT vs sessions vs Clerk one more time given the SSO
requirement. ... Okay, decided: we're going with Clerk for acme-platform
auth, specifically because of the SSO requirement. That replaces the
sessions decision from last week.

## Assistant

Got it, updating my understanding: acme-platform auth = Clerk, chosen for
SSO support, superseding the earlier sessions decision. And you're now on
the infra team full time rather than search. Want help scoping the Clerk
migration from the current Postgres-backed user table?

## User

Yes, let's scope that next. For now just wanted the decision logged clearly
since it's a reversal from what we said on the 12th.
