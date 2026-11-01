# A zebrafish tank of the lab's breeding stock is a "breeder" tank

The zebrafish database called the lab's shared breeding-stock tank
"breeding", while the mouse colony uses **Breeding** for a private mating cage
and **Breeder** for the shared stock. The same word meant opposite things, and
decided who may edit a tank. The lab's maintainer chose to rename the fish
purpose to "breeder", to match the colony, rather than document the
difference: revision 0054 rewrites stored tank purposes, keeping who may
edit each tank, and "breeding" typed or sent through the API still reads as
breeder (`models.TANK_PURPOSE_ALIASES`). A fish tank being crossed is
"mating", as before.

## Considered options

- Keep "breeding" for fish and document it in GLOSSARY.md: no data change,
  but people and assistants moving between the two databases would keep
  reading the word the other way.
- Make a fish "breeding" tank private, like a mouse Breeding cage: changes who
  may edit tanks in every lab.
