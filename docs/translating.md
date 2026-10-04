# Translating il2ks

The site speaks English, Russian, German, Spanish, French and Brazilian Portuguese. The first drafts of every
non-English language were **machine-translated** and are waiting for a native speaker to review them. This page is for
you if you want to review or improve a language, add a new one, or keep the translations in step after the pages change.

Two kinds of text are translated, and they live in different places:

| What | Where | Format |
|---|---|---|
| The **interface** (buttons, headings, table columns, messages, the admin) | `src/il2ks/locale/<language>/LC_MESSAGES/django.po` | gettext `.po` |
| The **names of game objects** (trucks, fences, barracks, crew; aircraft type names mostly stay as they are) | `src/il2ks/core/catalog/data/object_names_<language>.csv` | CSV |

Language folders: `ru`, `de`, `es`, `fr`, `pt_BR` (the site calls Brazilian Portuguese `pt-br`).

## Reviewing or improving a language

You need nothing but a text editor (a `.po` editor such as Poedit also works). Pick a language and open its two files.

### The interface (`django.po`)

Every entry looks like this:

```
# llm-draft
#, python-format
msgid "Data updated %(when)s"
msgstr "Daten aktualisiert: %(when)s"
```

- `msgid` is the English text. Never change it (it is how the site finds the translation).
- `msgstr` is the translation. Change it as you like.
- **Keep placeholders exactly as they are**: `%(when)s`, `%(n)d`, `%s`, `{}`. A test rejects a translation that loses or
  renames one. Their position in the sentence is yours to choose.
- `# llm-draft` marks a machine translation nobody has checked. **Delete that line when you have reviewed the entry.**
  Entries without it count as reviewed. Do not use the `fuzzy` flag: it hides the entry from the site.
- Entries with `msgid_plural` need one `msgstr[n]` per plural form of the language (Russian has three, the others two).
- An empty `msgstr ""` is not an error: the English text is shown until someone translates it.

Terms used in the game are chosen on purpose: *sortie* (one flight from take-off to landing or loss), *bailed out*,
*shot down*, *ditched*, *strafed*, *taxi accident*. Use the word your community uses for these. **REDFOR and BLUFOR
stay as they are** (the server owner can rename them in the admin), and so do aircraft type names (F-86A, MiG-15bis).
Page text is read by pilots, not by lawyers: short and plain beats formal.

**How the site addresses the reader** (maintainer, 2026-10-04): German **du**; French the polite **vous**; Russian **вы**
(lowercase, the usual UI convention); Spanish **tú**; Brazilian Portuguese **você**. Keep it consistent within a language.

**Translate for meaning in context, not word for word.** The goal is a GUI a native-speaking flight-sim player understands at a
glance, not literal fidelity.

- Before you translate a string, find where it is used (search the templates and `web/*.py` for the English text). A column
  header, a nav label, a button, a tooltip, a notice and the admin help text each need a different register and length;
  one-word strings such as *Kills*, *Lost*, *Air*, *Ground*, *Left*, *Used*, *Credit*, *Spawn*, *Seat* are ambiguous
  without that context.
- Use the wording a player expects on a stats site (what the column really means). Keep column headers and navigation
  labels short, so tables and the header do not break.
- Flavor lines (`web/flavor.py`) are jokes: rewrite them the way a native player would say them, do not render them.
- If an English source string is ambiguous, fix it at the source rather than guessing: add `{# Translators: ... #}` right
  before the `{% translate %}` in a template, or `# Translators: ...` right before the gettext call in Python; when the same
  English word needs different translations in different places use `pgettext` / `{% translate "..." context "..." %}`.
  Then run `uv run il2ks dev translations update` (and `bump-templates` if you touched a template).

See how much is left:

```
uv run il2ks dev translations status
```

```
language  strings translated llm-draft reviewed missing
de            141        141       141        0       0
```

`reviewed` is what a human has checked; `llm-draft` is what is still waiting for one; `missing` has no translation yet.

After editing, compile and run the tests:

```
uv run il2ks dev translations compile
uv run pytest tests/unit/test_translations.py
```

To look at your work, start the site (`uv run il2ks web --dev`) and pick the language in the footer. The choice is
stored in a cookie (`django_language`), so it sticks.

### Game object names (`object_names_<language>.csv`)

```
log_name,english,display_name,review
Fence wire 100m,Fence wire 100m,Drahtzaun 100 m,llm-draft
```

- `log_name` is the name in the game's log: never change it.
- `english` is the English name from `objects.csv`, repeated for you to compare. A test keeps it in step, so leave it.
- `display_name` is the translation. Keep the variant letters and numbers (`A`, `B1`, `100 m`).
- `review` is `llm-draft` for a machine translation nobody has checked. **Empty it when you have reviewed the row.**
  (Any other value is rejected.)
- Objects that are not listed show their English name. Delete a row to fall back to English.

Names are shown in the viewer's language, in this order: the server admin's own name for the object (if they edited it
in the admin) → your translation → the English name → the raw log name. See "Server owners" below.

## After the pages change (the one command)

Whenever someone adds or rewords text in a template or in Python (`{% translate %}`, `gettext_lazy`):

```
uv run il2ks dev translations update
```

This re-reads every template and Python file, adds new strings (empty) to each language's `django.po`, drops strings
that no longer exist, **keeps every translation and its `llm-draft` mark**, and compiles the `.mo` files. Commit the
changed `.po` and `.mo` files. A test (`tests/unit/test_translations.py`) fails with this very command if a `.po` is
out of date, so it cannot be forgotten.

New strings stay untranslated (English shows) until somebody translates them. To have a first draft made:

```
uv run il2ks dev translations missing de > missing_de.json     # the untranslated strings, as JSON
# ... fill in the values (a person, or an LLM given the game terms above) ...
uv run il2ks dev translations import de missing_de.json        # fills only empty entries, marks them llm-draft
uv run il2ks dev translations compile
```

`import` never overwrites an existing translation and refuses a draft that breaks a placeholder. For plural messages the
JSON value is a list with one string per plural form.

## Adding a language

1. Add it to `LANGUAGES` in `src/il2ks/serving/djsettings.py` (Django language code, English name, e.g. `("it", "Italian")`),
   and to `TARGET_LANGUAGES` in `src/il2ks/devtools/translations.py` (code, folder name, and the language's gettext
   plural rule; the [GNU plural forms table](https://www.gnu.org/software/gettext/manual/html_node/Plural-forms.html)
   lists them). A test fails if the two lists disagree.
2. `uv run il2ks dev translations update` creates `src/il2ks/locale/<folder>/LC_MESSAGES/django.po` and `django.mo`.
3. Translate it (see above). Django already ships its own translations of the admin's built-in texts for most languages.
4. Optionally create `src/il2ks/core/catalog/data/object_names_<code>.csv` for the object names (copy a header row).

## How it works, in short

- **No GNU gettext needed.** The commands are pure Python ([Babel](https://babel.pocoo.org/) reads and writes the
  catalogs; Django's own template parser finds the template strings), so they behave the same on Windows, Linux and in CI.
  Django's `makemessages` is not used because it needs the gettext tools installed.
- Templates use `{% translate %}` / `{% blocktranslate %}`, Python uses `gettext_lazy`; a test checks that no visible
  template text is left unwrapped.
- The compiled `.mo` files are committed (they are `.gitignore`d everywhere else) so that the published package
  contains them.
- The viewer's language comes from the footer menu (cookie `django_language`), otherwise from the browser's
  `Accept-Language` header, otherwise English. Cached pages are kept apart per language.

## Server owners: game object names

The shipped names are the defaults, **in every language**. In the admin (**Game objects**) you can type a different name;
that is an *override*: it shows to everyone in every language, instead of the shipped names. (Overrides are English-only
text for now; per-language overrides are planned for when someone needs them.) A catalog update from a new il2ks
version refreshes the names you did not touch, and never replaces one you edited. To go back to the shipped names,
select the objects and use **Reset display names to the catalog defaults**, or type the original name back.
