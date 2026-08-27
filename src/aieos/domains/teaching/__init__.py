"""Teaching domain (TOS-DEV02 Lane B).

Owns the durable teacher-owned Teaching Work preparation container. Teaching
Intent is the request that enters Work creation; it is never a durable
aggregate or a PostgreSQL table. Today's Mission is a derived projection with
no table of its own.
"""
