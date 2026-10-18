# Tags
Tags are saved snippets your server can post with `/tags use` or by typing the chat prefix and the name, like `?rules`. They can hold text, embeds, or Components V2 containers, take arguments, store values, make decisions, and give or take roles.

## Managing tags
Creating, editing, renaming, and deleting need Manage Messages.

**/tags create**
Saves a new tag. `name` and `content` are required. The other options are described below.

**/tags edit**
Changes any of `content`, `args`, `permission`, `cooldown`, or `actions`. Leave out what you don't want to change. Pass an empty `args` or `actions` to remove them.

**/tags rename**, **/tags delete**, **/tags info**, **/tags list**, **/tags search**
Rename or delete a tag, show how one is set up, list the server's tags, or search tag names.

Tag names use letters, numbers, hyphens, and underscores, up to 32 characters.

## Brackets and braces
Every special part of a tag uses curly braces `{}`, because round brackets and square brackets already mean something in Discord formatting. Old tags using `[embed]`, `$rand[1-6]`, and similar are converted to braces automatically the next time Melvin starts.

## Plain text, embeds, and containers
A tag is plain text unless its first line is `{embed}` or `{container}`.

**Embed**
Start with `{embed}`, then one key per line:
- `title:` up to 256 characters
- `description:` up to 4096 characters, can run on several lines
- `url:` a link for the title
- `color:` a hex code, like `#F4A261`
- `image:` a large image link
- `thumbnail:` a small image link
- `footer:` up to 2048 characters
- `field: name | value` up to 25 fields, names up to 256 characters and values up to 1024

An embed needs a title, a description, or a field.

**Container (Components V2)**
Start with `{container}`. Lines are text unless they're a key below. Text can be up to 4000 characters in total.
- `color:` a hex code for the accent bar
- `button: label | link` up to 5 link buttons, added under the text
- `{separator}` a small divider
- `{separator:small}` the same, written out
- `{separator:large}` a large divider

Any line that isn't a key is treated as text. A line that starts with `title:` in a container is text, so it can't set a title.

**Example**
```
{container}
## Welcome to the server
Read the rules first.
{separator}
Questions? Ask in the help channel.
button: Rules | https://example.com/rules
```

## Arguments
`/tags create` and `/tags edit` take an `args` list of `name:type`, separated by spaces. In the tag, use them as `$1`, `$2`, and so on, in the order given.

| Type | Takes |
|------|-------|
| `word` | One word. |
| `number` | A whole number. Add a range like `amount:number:1-100`. |
| `member` | A member, as a mention or ID. |
| `role` | A role, as a mention or ID. |
| `channel` | A channel, as a mention or ID. |
| `text` | The rest of the message. Must be the last argument. |

Add `?` after the name to make an argument optional, like `reason?:text`. Required arguments come before optional ones. Wrap a value in quotes to keep spaces inside it. If a value doesn't check out, Melvin says what's wrong and doesn't post the tag.

Tags without an argument list take whatever follows the name, so `$1` still works without one.

## Variables
Variables are filled in when the tag is posted.

| Variable | Result |
|----------|--------|
| `$1`, `$2`, ... | The argument in that position. Empty if there isn't one. |
| `$2+` | That argument and every argument after it. |
| `$*` | Every argument. |
| `$$` | A literal `$`. |
| `$user` | A mention of the person who used the tag. |
| `$server` | The name of this server. |

## Stored variables
A line like `$name = value` saves a value for the rest of this use of the tag. The value can use any other variable or function, and the line itself doesn't show in the post. Each use of the tag starts fresh.

```
$userscore = $roll{2d6}
$opponentscore = $roll{2d6}
$user rolled a $userscore and $1 rolled a $opponentscore
```

Stored variable names use lowercase letters, numbers, and underscores, and must start with a letter. Built-in names like `user`, `server`, `time`, `roll`, and `pick` can't be used. A stored variable that was never set shows as its name in the post, such as `$score`.

## Conditions
Use `{if}`, `{elif}`, `{else}`, and `{endif}` to choose what the tag posts.

```
{if $userscore > $opponentscore}$user wins!{elif $userscore < $opponentscore}$1 wins!{else}It's a tie!{endif}
```

- Comparisons are `==`, `!=`, `<`, `>`, `<=`, and `>=`.
- If both sides are whole numbers, they're compared as numbers. Otherwise they're compared as text.
- A condition on its own is true unless it's empty or `0`.
- A variable that was never set counts as empty.
- Blocks can be nested, and can go in plain text, embeds, and containers.
- A condition can use functions, like `{if $roll{1-6} > 3}`.

## Functions
Functions go in braces after a `$` name.

| Function | Result |
|----------|--------|
| `$rand{1-6}` | A random whole number from 1 to 6 (inclusive). Each use picks again. |
| `$pick{red\|green\|blue}` | One of the options, picked at random. Options are separated by `\|`. |
| `$shuffle{a\|b\|c}` | The options in a random order, joined with commas. |
| `$roll{2d6}` | The total of rolling 2 six-sided dice. Up to 20 dice, with up to 1000 sides. |
| `$upper{text}` | The text in capitals. Variables inside still work. |
| `$lower{text}` | The text in lower case. Variables inside still work. |

**Example**
Tag `greet` with the text `Welcome, $1! $2+`
Input: `/tags use name:greet args:Sam glad you could make it`
Output: `Welcome, Sam! glad you could make it`

## Timestamps
| Variable | Result |
|----------|--------|
| `$time` | Date and time, like "January 6, 2025 2:00 PM". |
| `$time:R` | Relative time, like "in 2 hours". |
| `$time:t` | Short time, like "14:00". |
| `$time:T` | Long time, like "14:00:00". |
| `$time:d` | Short date. |
| `$time:D` | Long date. |
| `$time:f` | Long date and short time (same as `$time`). |
| `$time:F` | Like `$time`, with the day of the week added. |

Timestamps are shown in each reader's own timezone.

## Permissions
`permission` decides who can use the tag.
- `none`: everyone.
- A permission like `manage_messages`. Administrators can always use it.
- A role, as a mention or ID. Only members with that role can use it.

## Cooldowns
`cooldown` is the number of seconds each member waits between uses, from 0 to 86400. It only starts after the tag posts.

## Actions
`actions` gives or takes roles after the tag posts, one per line:

```
addrole $user @Member
removerole $1 @Muted
```

Each line is `addrole` or `removerole`, then a member and a role. The member can be `$user`, `$1`, a mention, or an ID. The role can be a mention, an ID, or an argument.

Only members with Manage Roles can use a tag with actions. Melvin skips roles at or above its own top role or the user's top role, and roles that are managed or @everyone.

## Using tags
- `/tags use name:... args:...` posts a tag.
- In chat, type the prefix and the name, like `?rules`. The prefix is set in the tags module's settings and is `?` by default. The tag replies to your message.
