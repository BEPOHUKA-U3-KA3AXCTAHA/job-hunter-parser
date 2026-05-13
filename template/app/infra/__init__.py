"""Infrastructure layer.

Things that talk to the outside world (databases, HTTP, message queues,
filesystem). NEVER imports from `app.modules` or `app.entrypoints` —
infra is the bottom of the stack and must remain depend-able-on without
creating cycles.

If you find yourself wanting to import a domain model here, that's a
signal the domain model should move down to infra, OR the code you're
writing belongs in an adapter (one layer up).
"""
