# Team and rooms

## Sub-features

Grouped board, group creation/rename/delete, zoom/drag targets, team mentions,
room creation/group assignment, membership and conversation actions.

## How to get to it (user POV)

Choose Team for the board/creation menu. Conversation's sidebar lists rooms and a
plus button. At compact width open the member picker, then Rooms. Open a room and
use its header menu for rename/membership/delete.

## Driving it with look

```powershell
npm run look -- team team-group-menu team-rename team-delete team-new-menu team-new-group team-mentions team-empty team-drop-target team-zoom --theme both --width 1120,720
npm run look -- sidebar compact-rooms new-room room-group-menu room room-draft room-menu room-rename room-members room-delete room-hover --theme both --width 1120,720
npm run look -- room-composer-empty room-text room-composer-hover team-composer-empty team-text team-composer-hover --theme both --width 1120,720
```

Keep board/picker/thread/dialog PNGs. Check membership, authors, mention lists,
composer and overlay clearance. For mutation work extend scenes through create,
send, rename and membership changes; assert UI and mock boundary records. Native
proof needs a harmless room message, actual recipient responses and persistence
after reopening the isolated dev instance. Record recipients, states and cleanup.

## Gotchas

Room 1 has messages; room 2 is a team room. The compact picker is a different entry
point and needs 720 coverage. Synthetic drag scenes do not prove pointer/keyboard
drag accessibility. A confirmation dialog is not proof of deletion or delivery.
