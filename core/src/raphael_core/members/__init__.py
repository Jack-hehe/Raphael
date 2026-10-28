"""The members of the owner's team: assistants the owner makes beside Raphael, each with a name, a
role, the owner's instructions, a picture, a group, a model, the tools it may use, a conversation and
a memory of its own, and routines it runs on a schedule.

- `store`: the members, their groups and routines, and which threads have news, in the database.
- `schedule`: a routine's schedule and the next time it is due.
- `service`: the RPC methods (`members.*`, `groups.*`, `routines.*`), the pictures, the tools
  Raphael reaches the members by (`ask_member`, `create_member`) and the scheduler of the routines.

A member is a parameter of the one assistant (`agent.speaker.Speaker`), not an assistant of its
own: it answers through the same router, answer, gate and background worker as Raphael.
"""
