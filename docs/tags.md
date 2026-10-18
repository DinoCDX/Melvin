# Tags Cog Documentation
Saved snippets for this server. Tags can hold plain text, variables, timestamps, embeds, and Components V2 layouts.
Command group name: `tags`

## Storage
Tags are stored per server in `data/tags.db`. When Melvin leaves a server, its tags are deleted.

## Writing a tag
A tag is plain text unless its first line is `[embed]` or `[container]`.

**Plain text**
Anything on one or more lines, up to 2000 characters.

**Embed**
Start with `[embed]`, then one key per line:
- `title:` up to 256 characters
- `description:` up to 4096 characters. Lines under it continue the description until the next key.
- `url:`, `image:`, `thumbnail:` links starting with http:// or https://
- `color:` a hex code like `#F4A261`
- `footer:` up to 2048 characters
- `field: name | value` up to 25 fields, names up to 256 characters and values up to 1024

An embed needs a title, a description, or a field.

**Container (Components V2)**
Start with `[container]`. Lines are text. `[separator]` adds a small divider, and `[separator:large]` adds a large one. Use `[separator:small]` to be explicit. Text can be up to 4000 characters in total.
- `color:` a hex code for the accent bar
- `button: label | link` up to 5 link buttons, added under the text

Any line that isn't a key is treated as text. A line that starts with `title:` in a container is text, so it can't set a title.

**Example**
```
[container]
## Welcome to the server
Read the rules first.
[separator]
Questions? Ask in the help channel.
button: Rules | https://example.com/rules
```

## Variables
Variables are filled in from the arguments given with the tag.

| Variable | Result |
|------|--------|
| `$1`, `$2`, ... | The argument in that position. Empty if there isn't one. |
| `$2+` | The argument in that position and every argument after it. |
| `$*` | Every argument. |
| `$$` | A literal `$`. |
| `$rand[1-6]` | A random whole number from 1 to 6, inclusive. Each use picks again. |
| `$pick[red\|green\|blue]` | One of the options, picked at random. Options are separated by `\|`. Each use picks again. |
| `$shuffle[a\|b\|c]` | The options in a random order, separated by commas. Options are separated by `\|`. |
| `$roll[2d6]` | The total of rolling that many dice with that many sides. Up to 20 dice, with up to 1000 sides. |
| `$upper[text]` | The text in capitals. Variables inside it still work. |
| `$lower[text]` | The text in lower case. Variables inside it still work. |
| `$user` | A mention of the person who used the tag. |
| `$server` | The name of this server. |

Arguments are split on spaces. Wrap an argument in quotes to keep spaces inside it, like `"blue sky"`.

**Example**
Tag `greet` with the text `Welcome, $1! $2+`.
Input: `/tags use name:greet args:Sam glad you could make it`
Output: `Welcome, Sam! glad you could make it`

## Timestamps
| Variable | Result |
|------|--------|
| `$time` | Date and time, like "January 6, 2025 2:00 PM". |
| `$time:R` | Relative time, like "in 2 hours". |
| `$time:t` | Short time, like "14:00". |
| `$time:d` | Short date. |
| `$time:D` | Long date. |
| `$time:F` | Like `$time`, with the day of the week added. |

Timestamps are shown in each reader's own timezone.

## Posting a tag
**/tags use**
Post a saved tag. The text is posted in the channel, visible to everyone.

**Parameters**
`name` (required): the tag to post. Autocompletes from the server's tags.
`args` (optional): values for the tag's variables, separated by spaces.

**Permissions**
None. Guild only.

**Behavior**
Mentions in the text are shown but never pinged. If the tag doesn't exist, the reply is only visible to you.

**Posting with a prefix**
A tag can also be posted by typing the prefix followed by its name, such as `?rules`. Anything after the name is used as the arguments, so `?greet Sam glad you could make it` fills in `$1` and `$2+`. The reply is linked to the message that used it.
The default prefix is `?`. Change it for a server with `/tags config`. The prefix must be 1 to 3 characters with no spaces. If the saved prefix isn't valid, `?` is used.
A prefix followed by something that isn't a tag does nothing. The tags module must be on in the server.

## /tags create
Save a new tag.

**Parameters**
`name` (required): letters, numbers, hyphens, and underscores, up to 32 characters. Names are case-insensitive, so `Rules` and `rules` are the same tag.
`content` (required): the text of the tag. See Writing a tag above.

**Permissions**
Requires Manage Messages. Guild only.

**Behavior**
A tag name can only be used once per server. The text is checked when you save it, so a line the tag can't use gives an error and nothing is saved.

## /tags edit
Change the text of an existing tag.

**Parameters**
`name` (required): the tag to change. Autocompletes from the server's tags.
`content` (required): the new text.

**Permissions**
Requires Manage Messages. Guild only.

## /tags delete
Delete a tag.

**Parameters**
`name` (required): the tag to delete. Autocompletes from the server's tags.

**Permissions**
Requires Manage Messages. Guild only.

## /tags list
List all of the server's tags, in alphabetical order. Long lists are split into pages with buttons.

Below the list is a dropdown to edit a tag. Picking a tag opens a form with its current text, so you can change it. The form also has a "Delete this tag" checkbox to remove it instead.

The dropdown shows the first 25 tags. To edit a tag beyond that, use `/tags edit`.

**Permissions**
Listing is open to everyone. Opening the edit form requires Manage Messages. Guild only.

## /tags config
Change the chat prefix for this server.

**Permissions**
Requires Manage Server. Guild only.

## Notes
Tag names are checked on every command, so a name with spaces or symbols is rejected with an error.
Tag text is stored exactly as typed, so the edit form shows what you wrote, not the rendered output.

## Arguments
`/tags create` and `/tags edit` take an `args` list of `name:type`, separated by spaces. Use them in the tag as `$1`, `$2`, and so on, in the order given.

| Type | Takes |
|------|-------|
| `word` | One word. |
| `number` | A whole number. Add a range like `amount:number:1-100`. |
| `member` | A member, as a mention or ID. |
| `role` | A role, as a mention or ID. |
| `channel` | A channel, as a mention or ID. |
| `text` | The rest of the message. Must be the last argument. |

Add `?` after the name to make an argument optional, like `reason?:text`. Required arguments come before optional ones. Wrap a value in quotes to keep spaces inside it. If a value doesn't check out, Melvin says what's wrong and doesn't post the tag.

Tags without an argument list take whatever follows the name, as before, so `$1` still works without one.

## Permissions
`permission` decides who can use the tag: `none` for everyone, a permission like `manage_messages` (administrators can always use it), or a role, as a mention or ID.

## Cooldowns
`cooldown` is the number of seconds each member waits between uses, from 0 to 86400. It only starts after the tag posts.

## Actions
`actions` gives or takes roles after the tag posts, one per line:

```
addrole $user @Member
removerole $1 @Muted
```

Each line is `addrole` or `removerole`, then a member and a role. The member can be `$user`, `$1`, a mention, or an ID. The role can be a mention, an ID, or an argument. Only members with Manage Roles can use a tag with actions. Melvin skips roles at or above its own top role or the user's top role, and roles that are managed or @everyone.

## Using tags from chat or /tags use
A tag runs from chat with the prefix, like `?rules`, or with `/tags use`. The chat prefix is set in the tags module's settings.
