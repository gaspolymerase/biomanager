# Molecular biology: the next round

The plasmid page now draws the plasmid itself (Overview, Sequence, Digest,
Verify, Primers): maps, every base and its protein, virtual digests and
gels with Dam/Dcm, the lab's primers that bind and a PCR, clones checked
by sequencing and accepted, site-directed mutagenesis from the Sequence tab
(`app/mutagenesis.py`), Assemble a plasmid's In-Fusion and Gateway
(`app/cloning.py`, `app/gateway.py`), Compare with… on the Overview
(`app/plasmid_compare.py`), and a sequence fetched by its Addgene number or
GenBank accession (`app/sequence_fetch.py`). Compared with what SnapGene offers, these are
what a lab would still reach for, in the order they are worth doing.
Each builds on code that is already there.

## 1. Editing in the app's own viewer

The Sequence tab already picks bases. Typing over a selection, inserting
and deleting bases, adding a feature or a primer from a selection, and
undo would let the Open Vector Editor bundle go: features that a change
runs through are shifted or cut as `cloning.carry_features` already does
for fragments, every save is a version (`app/plasmid_versions.py`), and
the editor's JavaScript (some 3 MB) stops loading on every plasmid page.

## Left out on purpose

- **Multiple sequence alignment** of many sequences or proteins: rare at
  the bench, and well served by tools made for it.
- **TA and TOPO cloning simulation**: the product is the insert in a known
  vector at a known place; a lab records it with Made from.
- **A secondary-structure viewer for primers**: the hairpin and self-dimer
  ΔG the primer panel gives answer the question a lab asks.
