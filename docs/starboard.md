# Starboard Cog Documentation
Posts popular messages to a starboard channel, so the best messages in a server stay easy to find.
Command group name: `starboard`

The module is off by default. Turn it on with `/modules` or the dashboard, then set a starboard channel with `/starboard config`.

## Storage
Settings are stored with the rest of Melvin's module settings, in `data/modules.db`. See the modules documentation for details.
Each starred message is recorded in a `starboard_posts` table in `data/starboard.db`, which tracks which post belongs to which message. Message content is never stored.
Recent vote counts are kept in memory for up to 10 minutes, for the 500 most recently counted messages. This keeps busy posts from re-reading every reaction on each change.
When the bot is removed from a server, that server's starboard records and settings are deleted.

## How messages are starred
Melvin counts the members who react to a message with a starring emoji. Reactions on the starboard post count the same as reactions on the original message, and a member is only counted once, however many starring reactions they've added to the message and its post.

Bots never count. A message's author only counts their own reaction when Self-stars is on.

Reactions are only counted in the source channels, if any are set. Threads count under their parent channel.

When a message reaches the threshold, Melvin posts it to the starboard channel. The post shows the count, the channel, the author, the message text, and a Jump to Message button. Images on the message appear in a gallery, up to 10 of them. Other files are listed as links, and any images beyond the gallery limit are listed there too. If the message has no attachments, Melvin uses the first image from a linked embed, such as a GIF. The count updates as reactions change. If the count drops below the threshold, the post is removed.

Some other things to know:

- Messages from bots are never starred.
- A message from an NSFW channel only goes to an NSFW starboard channel.
- Editing a starred message updates its post.
- Deleting the original message removes its post, even if the module is turned off.
- Melvin needs permission to send messages and attach files in the starboard channel. If it can't post, it skips the message without an error.
- When the module is turned off, reactions aren't counted. Existing posts stay until their original messages are deleted.

## Auto-react
When Auto-react is on, Melvin reacts to new messages in the source channels with the first starring emoji, so members can star a message with one click. Auto-react needs source channels, so it never reacts across a whole server. Melvin needs permission to add reactions in those channels. Its own reactions don't count toward the threshold.

## /starboard config
Change starboard settings for this server.

**Parameters**
None.

**Permissions**
Requires Manage Server. Guild only.

**Behavior**
The command replies with a settings card that lists each setting with its current value. On/off settings are buttons. Channels are picked from a select menu, and deselecting one clears it. Emojis and the threshold are typed into a form.
Each change saves straight away and the card updates in place. Only members with Manage Server can use the card, and it stops responding after 5 minutes.
The same settings can be changed from the dashboard on the website, under Config on the module's card.

**Settings**

| Setting | Description |
|------|--------------|
| Starboard channel | Where starred messages go. |
| Source channels | Only count reactions here. Empty means everywhere. Threads count under their parent channel. |
| Emojis | Reactions that star a message. Unicode or custom, up to 10, separated by spaces. Defaults to ⭐. Keycap emojis like 1️⃣ aren't supported. |
| Any emoji | On/off. When on, every reaction counts. Off by default. |
| Threshold | Reactions needed to post, from 1 to 100. Defaults to 3. |
| Self-stars | On/off. When on, a message's author counts their own reaction. Off by default. |
| Auto-react | On/off. When on, Melvin reacts to new messages in the source channels with the first starring emoji. Off by default. |

**Example**
Input: `/starboard config`, then pick `#best-of` as the starboard channel.
Output: The card updates to show `Set to #best-of`.