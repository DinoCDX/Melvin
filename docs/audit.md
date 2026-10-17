# Audit Cog Documentation
Guild audit logging. Once an audit channel is set, the bot posts a log message there whenever something notable happens in the guild. Events can also be sent to their own channels, see Log channels below.
Command group name: `audit`

## Storage
Settings are stored with the rest of Melvin's module settings, in `data/modules.db`. See the modules documentation for details.

## /audit config
Change audit log settings for this server.

**Parameters**
None.

**Permissions**
Requires Manage Server. Guild only.

**Behavior**
The command replies with a settings card listing each setting, what it does, and its current value. Channels and roles are picked from a select menu, and deselecting clears them. Other settings have an Edit button that opens a form, and leaving the form empty resets the setting to its default.
Each change saves straight away and the card updates in place. Only members with Manage Server can use the card, and it stops responding after 5 minutes.
The same settings can be changed from the dashboard on the website, under Config on the module's card.

**Settings**

| Setting | Description |
|------|--------------|
| Audit channel | Used for any event without its own channel. Nothing is logged until one is set. |
| Message channel | Message edits and deletes. Uses the audit channel if not set. |
| Member channel | Joins, leaves, bans, and member or profile changes. Uses the audit channel if not set. |
| Server channel | Server, channel, and role changes. Uses the audit channel if not set. |
| Other channel | Voice channel activity and anything else. Uses the audit channel if not set. |

**Example**
Input: `/audit config`, then pick `#mod-logs` as the message channel.
Output: The card updates to show `Set to #mod-logs`.

## Log channels
Each event belongs to one of four categories: message, member, server, or other. If the category has its own channel, the event is posted there. If not, it's posted to the audit channel. If neither is set, the event isn't logged.

For example, with only the audit channel set, everything goes there. Setting a message channel moves message edits and deletes to it, and everything else stays in the audit channel.

## Logged events
Every log message includes a timestamp and an accent color. Green is used for things being created or joining, red for things being deleted or leaving, and orange for changes.

**Members** (member channel)
Joins and leaves are logged with the member's name, avatar, and the guild's member count.
Member updates are logged when a member's nickname changes, roles are added or removed, or a timeout is applied or removed. Other member changes are ignored.
Profile updates are logged when a user changes their username, display name, or avatar. This is posted in every guild that shares the user and has logging enabled.
Bans and unbans are also logged.

**Messages** (message channel)
Edited messages are logged with the author, the channel, a "Jump to Message" button, any attachments, and the content before and after the edit. Edits that don't change the text are ignored.
Deleted messages are logged with the author, the channel, any attachments, and the deleted content.
Messages from bots and apps are ignored. Message content is cut to 500 characters, and any markdown in it is escaped. If a message has no text, for example when it only contains an embed, a placeholder is shown instead.

**Voice** (other channel)
Joining a voice channel, leaving one, and moving between voice channels are logged.

**Channels, roles, and server** (server channel)
Channels being created or deleted are logged, along with name and topic changes.
Roles being created or deleted are logged, along with name, color, and permission changes. Color changes also state the role's style, which is either Solid, Gradient, or Holographic.
Server updates are logged when the server's name or icon changes, or when ownership is transferred.

## Notes
Log messages never ping anyone.
Each log channel must be a text channel the bot can send messages in. If the bot can't post there, the event is skipped silently.
