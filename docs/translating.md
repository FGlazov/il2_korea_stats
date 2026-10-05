# Translating il2ks

The site speaks English, Russian, German, Spanish, French and Brazilian Portuguese. The first drafts of every
non-English language were **machine-translated** and are waiting for a native speaker to review them. This page is for
you if you want to review or improve a language, add a new one, or keep the translations in step after the pages change.

Two kinds of text are translated, and they live in different places:

| What | Where | Format |
|---|---|---|
| The **interface** (buttons, headings, table columns, messages, the admin) | `src/il2ks/locale/<language>/LC_MESSAGES/django.po` | gettext `.po` |
| The **names of game objects** (trucks, fences, barracks, crew; aircraft type names mostly stay as they are) | `src/il2ks/core/catalog/data/object_names_<language>.csv` | CSV |

| The **names of weapon modifications** (*Anti-G suit*, *Improved air brakes and wing*; designations such as NR-23 stay) | `src/il2ks/core/catalog/data/weapon_mod_names_<language>.csv` | CSV |

**What is translated and what is not** (maintainer, 2026-10-05): game data that describes *what a thing is* (object names, weapon-mod
names: the dimensions you filter and compare by) is translated; **mission names are facts** (what the server called the mission) and are
never translated. Aircraft type names mostly stay as they are.

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
French: a no-break space (U+00A0) before `:` `;` `!` `?` and inside `« »`.

**Recurring terms** (fixed 2026-10-05; use the same word everywhere a string mentions the concept, inflected as the grammar needs):

| English | ru | de | es | fr | pt_BR |
|---|---|---|---|---|---|
| Attack proficiency (ground score per hour on target) | мастерство штурмовки | Erdkampfstärke | competencia de ataque | maîtrise de l'attaque | proficiência de ataque |
| attack (role) / attack sortie | штурмовка / штурмовой вылет | Erdkampf / Erdkampfeinsatz | ataque / salida de ataque | attaque / sortie d'attaque | ataque / surtida de ataque |
| air superiority (role) | господство в воздухе | Luftüberlegenheit | superioridad aérea | supériorité aérienne | superioridade aérea |
| Tour | тур | Tour | temporada | tour | temporada |
| Role | роль | Rolle | función | rôle | função |
| Extra columns | дополнительные столбцы | zusätzliche Spalten | columnas adicionales | colonnes supplémentaires | colunas extras |
| Ironman | несгораемый | Unkaputtbar | A Prueba de Balas | Increvable | Duro na Queda |
| Old Hand | старожил | Alter Hase | Veterano de la Casa | Vieux de la Vieille | Veterano de Casa |
| Live | идёт | läuft | en curso | en cours | ao vivo |
| Flight time | время в полёте | Flugzeit | tiempo de vuelo | temps de vol | tempo de voo |
| Encounters (Elo games) | столкновения | Begegnungen | encuentros | rencontres | confrontos |
| Loadout (weapons carried) | вооружение | Bewaffnung | configuración de armamento | configuration d'emport | configuração de armamento |
| Modifications | модификации | Modifikationen | modificaciones | modifications | modificações |
| Hits to destroy | попаданий до уничтожения | Treffer bis zur Zerstörung | impactos para destruir | impacts pour détruire | acertos para destruir |
| Repaired after landing | починен после посадки | nach der Landung repariert | reparado tras aterrizar | réparé après l'atterrissage | reparada após o pouso |

The Russian Elo is written **Эло** everywhere. A career medal's tour variant is the medal name plus the language's tour word
(ru/de/fr: "Name (тур / Tour / Tour)"; es/pt_BR: "Name de la Temporada / da Temporada").

**Translate for meaning in context, not word for word.** The goal is a GUI a native-speaking flight-sim player understands at a
glance, not literal fidelity.

**Quips, flavor lines and medal one-liners: idiomatic first** (maintainer, 2026-10-04). A quip does not have to be a translation of
the English line. Write the best line for that language and situation in the same tone, with the language's own humour, idioms and
aviation slang; translate literally only as a last resort.

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
de           1070       1070      1054       16       0
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

### Weapon modification names (`weapon_mod_names_<language>.csv`)

```
english,display_name,review
Anti-G suit,Anti-g-Anzug,llm-draft
```

- `english` is the name in `weapon_mods.csv` (the key; one name is shared by several aircraft, so it is translated once). Never
  change it; a test keeps the file in step with `weapon_mods.csv`, one row per name, sorted.
- `display_name` is the translation: use the term pilots of that language use for the equipment (Russian: the established Soviet
  terms, e.g. *противоперегрузочный костюм*). Keep designations (NR-23, K-14C, AN/APS-13, 150) as they are; Soviet ones are written
  in Cyrillic in Russian like the aircraft (НР-23, АСП-3Н).
- `review` works as for object names: `llm-draft` until a person has checked the row, then empty.
- The name is shown in the viewer's language on the sortie page, the aircraft page's filter and its modifications table (a set such
  as *NR-23 cannons + Anti-G suit* joins the translated parts with ` + `). A name with no row shows in English; an id the catalog
  does not list shows as its raw id. When a new mod is added to `weapon_mods.csv`, add its row to all five files (a test fails until you do).

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
4. Optionally create `src/il2ks/core/catalog/data/object_names_<code>.csv` for the object names and
   `weapon_mod_names_<code>.csv` for the weapon-mod names (copy a header row; the second needs a row for every mod name).

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
